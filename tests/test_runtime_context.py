"""A2 runtime-input and isolated bounded-history tests (offline)."""

import pytest

from kapibala.adapters.base import LLMError
from kapibala.adapters.fake import FakeAdapter
from kapibala.agent import ScreeningAgent
from kapibala.audit import AuditLog
from kapibala.executor import Executor
from kapibala.followup import Followup, FollowupQueue
from kapibala.rate_limiter import SlidingWindowRateLimiter
from kapibala.reply_generator import FOLLOWUP_TEMPLATE, TemplateReplyGenerator
from kapibala.runtime import (
    ConversationRole,
    ConversationStore,
    RuntimeConfig,
)
from kapibala.schemas import Estimation, Intent
from kapibala.state_machine import SessionState, StateMachine


class FakeClock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


@pytest.fixture
def rig():
    clock = FakeClock()
    state_machine = StateMachine()
    limiter = SlidingWindowRateLimiter(clock=clock)
    sent: list[tuple[str, str]] = []
    audit = AuditLog(clock=clock)
    followups = FollowupQueue()
    executor = Executor(
        state_machine,
        limiter,
        lambda customer_id, text: sent.append((customer_id, text)),
        audit,
        followups,
    )
    adapter = FakeAdapter()
    history = ConversationStore(max_turns=10)
    agent = ScreeningAgent(
        adapter,
        TemplateReplyGenerator(),
        executor,
        state_machine,
        audit,
        followups,
        clock=clock,
        runtime_config=RuntimeConfig(max_message_length=12, max_history_turns=10),
        conversation_store=history,
    )
    return agent, adapter, state_machine, sent, followups, history, clock


def estimation(intent: Intent = Intent.INTERESTED, **kwargs) -> Estimation:
    return Estimation(intent=intent, **kwargs)


def contents(history: ConversationStore, customer_id: str):
    return [(turn.role, turn.content) for turn in history.get(customer_id)]


@pytest.mark.parametrize(
    ("customer_id", "message", "reason"),
    [
        (None, "hello", "invalid_customer_id"),
        ("   ", "hello", "invalid_customer_id"),
        ("c1", None, "blank_message"),
        ("c1", " \t\n", "blank_message"),
        ("c1", "1234567890123", "message_too_long"),
    ],
)
def test_invalid_input_is_rejected_before_llm_state_or_history(
    rig, customer_id, message, reason
):
    agent, adapter, state_machine, sent, _, history, _ = rig

    result = agent.handle_message(customer_id, message)

    assert result.note == "invalid_input"
    assert result.input_error == reason
    assert adapter.calls == []
    assert state_machine._states == {}
    assert history.get("c1") == ()
    assert sent == []


def test_conversation_store_is_bounded_ordered_and_customer_isolated():
    history = ConversationStore(max_turns=3)
    history.append_customer("c1", "one")
    history.append_assistant("c1", "two")
    history.append_customer("c2", "private")
    history.append_customer("c1", "three")
    history.append_assistant("c1", "four")

    assert contents(history, "c1") == [
        (ConversationRole.ASSISTANT, "two"),
        (ConversationRole.CUSTOMER, "three"),
        (ConversationRole.ASSISTANT, "four"),
    ]
    assert contents(history, "c2") == [(ConversationRole.CUSTOMER, "private")]
    assert history.get("unknown") == ()


def test_success_records_reply_without_injecting_existing_history_into_classifier(rig):
    agent, adapter, state_machine, sent, _, history, _ = rig
    history.append_customer("c1", "previous untrusted context")
    adapter.script(estimation())

    result = agent.handle_message("  c1  ", "hello")

    assert result.executions[0].reason == "sent"
    assert adapter.calls == ["hello"]
    assert "c1" in state_machine._states
    assert "  c1  " not in state_machine._states
    assert contents(history, "c1") == [
        (ConversationRole.CUSTOMER, "previous untrusted context"),
        (ConversationRole.CUSTOMER, "hello"),
        (ConversationRole.ASSISTANT, sent[0][1]),
    ]


def test_fail_closed_keeps_only_the_accepted_inbound_message(rig):
    agent, adapter, _, sent, _, history, _ = rig
    adapter.script(LLMError("timeout"))

    result = agent.handle_message("c1", "hello")

    assert result.note == "fail_closed"
    assert sent == []
    assert contents(history, "c1") == [(ConversationRole.CUSTOMER, "hello")]


def test_rate_limited_draft_is_not_stored_as_an_assistant_turn(rig):
    agent, adapter, _, sent, _, history, _ = rig
    adapter.script(estimation())
    adapter.script(estimation())

    first = agent.handle_message("c1", "first")
    second = agent.handle_message("c1", "second")

    assert first.executions[0].reason == "sent"
    assert second.executions[0].reason == "rate_limited"
    assert len(sent) == 1
    assert contents(history, "c1") == [
        (ConversationRole.CUSTOMER, "first"),
        (ConversationRole.ASSISTANT, sent[0][1]),
        (ConversationRole.CUSTOMER, "second"),
    ]


def test_escalating_message_is_stored_but_later_silent_message_is_not(rig):
    agent, adapter, state_machine, _, _, history, clock = rig
    adapter.script(estimation(intent=Intent.OFF_TOPIC))
    adapter.script(estimation(intent=Intent.OFF_TOPIC))

    agent.handle_message("c1", "first")
    clock.advance(60)
    escalated = agent.handle_message("c1", "second")
    silent = agent.handle_message("c1", "third")

    assert escalated.transition.escalated_now is True
    assert silent.note == "silent_escalated"
    assert state_machine.get("c1").session is SessionState.ESCALATED
    customer_messages = [
        content
        for role, content in contents(history, "c1")
        if role is ConversationRole.CUSTOMER
    ]
    assert customer_messages == ["first", "second"]


def test_closing_message_is_stored_but_later_silent_message_is_not(rig):
    agent, adapter, state_machine, sent, _, history, _ = rig
    adapter.script(estimation(intent=Intent.REJECTED))

    closed = agent.handle_message("c1", "no thanks")
    silent = agent.handle_message("c1", "hello")

    assert closed.transition.closed_now is True
    assert silent.note == "silent_closed"
    assert state_machine.get("c1").session is SessionState.CLOSED
    assert sent == []
    assert contents(history, "c1") == [
        (ConversationRole.CUSTOMER, "no thanks")
    ]


def test_only_a_successfully_sent_followup_is_added_to_history(rig):
    agent, _, _, sent, followups, history, clock = rig
    followups.add(Followup(customer_id="c1", due_at=clock.t, context="existing"))

    outcomes = agent.run_followups()

    assert outcomes[0][1].reason == "sent"
    assert sent == [("c1", FOLLOWUP_TEMPLATE)]
    assert contents(history, "c1") == [
        (ConversationRole.ASSISTANT, FOLLOWUP_TEMPLATE)
    ]


@pytest.mark.parametrize("max_turns", [0, -1])
def test_runtime_limits_must_be_positive(max_turns):
    with pytest.raises(ValueError):
        ConversationStore(max_turns=max_turns)
    with pytest.raises(ValueError):
        RuntimeConfig(max_history_turns=max_turns)
