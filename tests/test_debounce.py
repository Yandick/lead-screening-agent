"""防抖聚合层测试（含与 agent 管道的集成）。"""

import pytest

from kapibala.adapters.fake import FakeAdapter
from kapibala.agent import ScreeningAgent
from kapibala.audit import AuditLog
from kapibala.debounce import MessageDebouncer
from kapibala.executor import Executor
from kapibala.followup import FollowupQueue
from kapibala.rate_limiter import SlidingWindowRateLimiter
from kapibala.reply_generator import TemplateReplyGenerator
from kapibala.schemas import Estimation, Intent
from kapibala.state_machine import SessionState, StateMachine


class FakeClock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def make_debouncer(clock=None, window=3.0):
    batches: list[tuple[str, str]] = []
    clock = clock or FakeClock()
    debouncer = MessageDebouncer(
        lambda cid, text: batches.append((cid, text)) or text, clock=clock, window_seconds=window
    )
    return debouncer, batches, clock


def test_burst_merges_into_one_batch_after_silence():
    d, batches, clock = make_debouncer()
    d.feed("c1", "在吗")
    d.feed("c1", "你们那个系统")
    d.feed("c1", "多少钱")
    assert d.flush_due() == []  # 静默期未满
    clock.advance(3)
    results = d.flush_due()
    assert len(results) == 1
    assert batches == [("c1", "在吗\n你们那个系统\n多少钱")]


def test_new_message_resets_silence_timer():
    d, batches, clock = make_debouncer()
    d.feed("c1", "第一条")
    clock.advance(2)
    d.feed("c1", "第二条")  # 重新计时
    clock.advance(2)
    assert d.flush_due() == []  # 距最后一条才 2 秒
    clock.advance(1)
    assert len(d.flush_due()) == 1
    assert batches == [("c1", "第一条\n第二条")]


def test_customers_flush_independently():
    d, batches, clock = make_debouncer()
    d.feed("c1", "c1 的消息")
    clock.advance(2)
    d.feed("c2", "c2 的消息")
    clock.advance(1.5)  # c1 静默 3.5s，c2 静默 1.5s
    results = d.flush_due()
    assert len(results) == 1
    assert results[0][0] == "c1"
    assert d.pending_count("c2") == 1


def test_flush_customer_ignores_window():
    d, batches, _ = make_debouncer()
    d.feed("c1", "立即处理我")
    result = d.flush_customer("c1")
    assert result is not None
    assert batches == [("c1", "立即处理我")]
    assert d.flush_customer("c1") is None  # 已清空


@pytest.fixture
def agent_rig():
    clock = FakeClock()
    sm = StateMachine()
    limiter = SlidingWindowRateLimiter(clock=clock)
    sent: list[tuple[str, str]] = []
    audit = AuditLog(clock=clock)
    followups = FollowupQueue()
    executor = Executor(sm, limiter, lambda cid, text: sent.append((cid, text)), audit, followups)
    adapter = FakeAdapter()
    agent = ScreeningAgent(
        adapter, TemplateReplyGenerator(), executor, sm, audit, followups, clock=clock
    )
    debouncer = MessageDebouncer(agent.handle_message, clock=clock, window_seconds=3.0)
    return debouncer, adapter, sm, sent, clock


def test_burst_runs_pipeline_once(agent_rig):
    """连发 3 条只产生一次 LLM 调用、一次状态推进、一条回复。"""
    debouncer, adapter, _, sent, clock = agent_rig
    adapter.script(Estimation(intent=Intent.NEEDS_INFO))
    debouncer.feed("c1", "在吗")
    debouncer.feed("c1", "你们那个系统")
    debouncer.feed("c1", "多少钱")
    clock.advance(3)
    debouncer.flush_due()
    assert len(adapter.calls) == 1
    assert adapter.calls[0] == "在吗\n你们那个系统\n多少钱"
    assert len(sent) == 1


def test_off_topic_burst_counts_once(agent_rig):
    """连发的碎片只算一次异常计数，不会因打字快被误升级。"""
    debouncer, adapter, sm, _, clock = agent_rig
    off = Estimation(intent=Intent.OFF_TOPIC)
    adapter.script(off)
    adapter.script(off)
    debouncer.feed("c1", "哈哈")
    debouncer.feed("c1", "吃了吗")
    debouncer.feed("c1", "下雨了")
    clock.advance(3)
    debouncer.flush_due()
    assert sm.get("c1").anomaly_count == 1  # 一批只计一次
    assert sm.get("c1").session is SessionState.ACTIVE
    debouncer.feed("c1", "又下雨")
    clock.advance(3)
    debouncer.flush_due()
    assert sm.get("c1").session is SessionState.ESCALATED  # 两批共两次才升级
