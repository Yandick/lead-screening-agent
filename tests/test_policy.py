"""策略层测试：真值表全覆盖（plan 测试清单 1、2 的策略映射部分）。"""

import pytest

from kapibala.policy import decide
from kapibala.schemas import Action, Estimation, Intent, ReplyKind
from kapibala.state_machine import Transition

NO_TRANSITION = Transition()


def est(intent=Intent.INTERESTED, **kw) -> Estimation:
    return Estimation(intent=intent, **kw)


@pytest.mark.parametrize(
    "intent,expected_kind",
    [
        (Intent.INTERESTED, ReplyKind.PITCH),
        (Intent.NEEDS_INFO, ReplyKind.ANSWER),
        (Intent.OFF_TOPIC, ReplyKind.REDIRECT),
        (Intent.OTHER, ReplyKind.GENERIC),
    ],
)
def test_every_intent_maps_to_legal_action(intent, expected_kind):
    d = decide(est(intent=intent), NO_TRANSITION)
    assert d.actions == (Action.REPLY,)
    assert d.reply_kind is expected_kind
    assert all(isinstance(a, Action) for a in d.actions)


def test_rejected_maps_to_mark_not_interested():
    d = decide(est(intent=Intent.REJECTED), Transition(closed_now=True))
    assert d.actions == (Action.MARK_NOT_INTERESTED,)


def test_dissatisfied_is_orthogonal_to_intent():
    """有兴趣但很不满：安抚优先于推进转化（清单 2）。"""
    d = decide(est(intent=Intent.INTERESTED, dissatisfied=True), NO_TRANSITION)
    assert d.actions == (Action.REPLY,)
    assert d.reply_kind is ReplyKind.SOOTHE


def test_escalated_now_maps_to_escalate_action():
    d = decide(est(intent=Intent.OFF_TOPIC), Transition(escalated_now=True))
    assert d.actions == (Action.ESCALATE_TO_HUMAN,)


def test_first_low_confidence_clarifies():
    d = decide(est(confidence=0.3), NO_TRANSITION)
    assert d.actions == (Action.REPLY,)
    assert d.reply_kind is ReplyKind.CLARIFY


def test_second_low_confidence_escalates():
    d = decide(est(confidence=0.3), Transition(escalated_now=True))
    assert d.actions == (Action.ESCALATE_TO_HUMAN,)


def test_interested_with_followup_request_schedules():
    d = decide(est(intent=Intent.INTERESTED, followup_requested=True), NO_TRANSITION)
    assert d.actions == (Action.REPLY, Action.SCHEDULE_FOLLOWUP)
    assert d.reply_kind is ReplyKind.CONFIRM_FOLLOWUP
