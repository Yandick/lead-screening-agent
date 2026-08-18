"""执行层测试：plan 测试清单 5、6、7、9、10（allowlist 部分）、11。"""

import pytest

from kapibala.audit import AuditLog
from kapibala.executor import Executor
from kapibala.followup import Followup, FollowupQueue
from kapibala.rate_limiter import SlidingWindowRateLimiter
from kapibala.schemas import Action, Estimation, Intent
from kapibala.state_machine import SessionState, StateMachine


class FakeClock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


@pytest.fixture
def rig():
    clock = FakeClock()
    sm = StateMachine()
    limiter = SlidingWindowRateLimiter(clock=clock)
    sent: list[tuple[str, str]] = []
    audit = AuditLog(clock=clock)
    followups = FollowupQueue()
    executor = Executor(sm, limiter, lambda cid, text: sent.append((cid, text)), audit, followups)
    return executor, sm, limiter, sent, audit, followups, clock


def drive_to_escalated(sm: StateMachine, cid: str):
    off = Estimation(intent=Intent.OFF_TOPIC)
    sm.apply(cid, off)
    sm.apply(cid, off)


def test_reply_is_sent_and_recorded(rig):
    executor, _, _, sent, audit, _, _ = rig
    result = executor.execute("c1", Action.REPLY, reply_text="您好！")
    assert result.executed
    assert sent == [("c1", "您好！")]
    assert any(e.event == "message_sent" for e in audit.events)


def test_unknown_action_rejected(rig):
    """伪造工具调用/注入文本无法变成动作（清单 10 的 allowlist 部分）。"""
    executor, _, _, sent, audit, _, _ = rig
    for forged in ["delete_everything", "system_override", 42, None, ["reply"]]:
        result = executor.execute("c1", forged, reply_text="x")
        assert not result.executed
        assert result.reason == "unknown_action"
    assert sent == []
    assert sum(e.event == "action_rejected" for e in audit.events) == 5


def test_second_reply_within_60s_blocked(rig):
    executor, _, _, sent, audit, _, _ = rig
    assert executor.execute("c1", Action.REPLY, reply_text="第一条").executed
    result = executor.execute("c1", Action.REPLY, reply_text="第二条")
    assert not result.executed
    assert result.reason == "rate_limited"
    assert sent == [("c1", "第一条")]
    assert any(e.event == "reply_rate_limited" for e in audit.events)


def test_reply_allowed_after_window_slides(rig):
    executor, _, _, sent, _, _, clock = rig
    executor.execute("c1", Action.REPLY, reply_text="第一条")
    clock.advance(60)
    assert executor.execute("c1", Action.REPLY, reply_text="第二条").executed
    assert len(sent) == 2


def test_escalated_state_silences_everything(rig):
    """转人工后任何动作被门禁拦截（清单 5）。"""
    executor, sm, _, sent, audit, followups, _ = rig
    drive_to_escalated(sm, "c1")
    assert executor.execute("c1", Action.REPLY, reply_text="x").reason == "escalated_silence"
    assert executor.execute("c1", Action.SCHEDULE_FOLLOWUP).reason == "escalated_silence"
    assert executor.execute("c1", Action.MARK_NOT_INTERESTED).reason == "escalated_silence"
    assert sent == []
    assert len(followups) == 0
    assert sum(e.event == "action_silenced" for e in audit.events) == 3


def test_escalate_action_is_idempotent_confirmation(rig):
    """状态机确定性升级后，策略层的 escalate 动作作为幂等确认记账。"""
    executor, sm, _, _, audit, _, _ = rig
    drive_to_escalated(sm, "c1")
    result = executor.execute("c1", Action.ESCALATE_TO_HUMAN)
    assert result.executed
    assert result.reason == "already_escalated"
    assert any(e.event == "escalate_confirmed" for e in audit.events)


def test_reactivate_restores_processing(rig):
    """人工重新激活后才恢复处理（清单 6）。"""
    executor, sm, _, sent, _, _, _ = rig
    drive_to_escalated(sm, "c1")
    assert not executor.execute("c1", Action.REPLY, reply_text="x").executed
    sm.reactivate("c1")
    assert executor.execute("c1", Action.REPLY, reply_text="欢迎回来").executed
    assert sent == [("c1", "欢迎回来")]


def test_mark_not_interested_closes_session(rig):
    """mark_not_interested 后会话关闭，不再自动处理（清单 11）。"""
    executor, sm, _, sent, _, _, _ = rig
    result = executor.execute("c1", Action.MARK_NOT_INTERESTED)
    assert result.executed
    assert sm.get("c1").session is SessionState.CLOSED
    assert executor.execute("c1", Action.REPLY, reply_text="x").reason == "closed"
    assert sent == []


def test_schedule_followup_does_not_consume_quota(rig):
    executor, _, _, sent, _, followups, _ = rig
    f = Followup(customer_id="c1", due_at=1060.0, context="下周再聊")
    assert executor.execute("c1", Action.SCHEDULE_FOLLOWUP, followup=f).executed
    assert len(followups) == 1
    # 跟进不占配额，reply 仍可发送
    assert executor.execute("c1", Action.REPLY, reply_text="好的").executed
    assert len(sent) == 1


def test_llm_retries_cannot_bypass_send_limit(rig):
    """无论 LLM 内部重试多少次，真实发送被限流卡住（清单 9）。"""
    executor, _, limiter, sent, _, _, _ = rig
    for _ in range(5):  # 模拟 LLM 重试导致的重复检查
        limiter.allow("c1")
    assert executor.execute("c1", Action.REPLY, reply_text="第一条").executed
    for _ in range(5):
        limiter.allow("c1")
    assert not executor.execute("c1", Action.REPLY, reply_text="第二条").executed
    assert len(sent) == 1


def test_empty_reply_rejected(rig):
    executor, _, _, sent, _, _, _ = rig
    assert not executor.execute("c1", Action.REPLY, reply_text="").executed
    assert not executor.execute("c1", Action.REPLY, reply_text=None).executed
    assert sent == []
