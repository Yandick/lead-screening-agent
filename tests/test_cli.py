"""CLI 命令处理测试（轻量）。"""

import pytest

from kapibala.adapters.fake import FakeAdapter
from kapibala.agent import ScreeningAgent
from kapibala.audit import AuditLog
from kapibala.cli import CLI
from kapibala.executor import Executor
from kapibala.followup import FollowupQueue
from kapibala.rate_limiter import SlidingWindowRateLimiter
from kapibala.reply_generator import TemplateReplyGenerator
from kapibala.state_machine import StateMachine


class FakeClock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


@pytest.fixture
def cli_rig():
    clock = FakeClock()
    sm = StateMachine()
    limiter = SlidingWindowRateLimiter(clock=clock)
    sent: list[tuple[str, str]] = []
    audit = AuditLog(clock=clock)
    followups = FollowupQueue()
    executor = Executor(sm, limiter, lambda cid, text: sent.append((cid, text)), audit, followups)
    adapter = FakeAdapter()
    agent = ScreeningAgent(
        adapter, TemplateReplyGenerator(), executor, sm, audit, followups,
        clock=clock, followup_delay=3600.0,
    )
    return CLI(agent, sm, adapter, audit), sent, clock


def test_script_then_msg(cli_rig):
    cli, sent, _ = cli_rig
    out = cli.handle_line("script intent=interested confidence=0.9")
    assert "intent=interested" in out
    out = cli.handle_line("msg c1 你们产品我想了解一下")
    assert "intent=interested" in out
    assert "动作：reply" in out
    assert len(sent) == 1


def test_msg_shows_escalation_and_silence(cli_rig):
    cli, _, _ = cli_rig
    cli.handle_line("script intent=off_topic")
    cli.handle_line("msg c1 今天天气不错")
    cli.handle_line("script intent=off_topic")
    out = cli.handle_line("msg c1 你吃饭了吗")
    assert "escalated" in out
    out = cli.handle_line("msg c1 还在吗")
    assert "静默" in out


def test_show_state_and_reactivate_and_reset(cli_rig):
    cli, _, _ = cli_rig
    cli.handle_line("script intent=off_topic")
    cli.handle_line("msg c1 呵呵")
    out = cli.handle_line("show_state c1")
    assert "session=active" in out and "anomaly_count=1" in out
    out = cli.handle_line("reactivate c1")
    assert "重新激活" in out
    out = cli.handle_line("reset c1")
    assert "已清空" in out
    assert "anomaly_count=0" in cli.handle_line("show_state c1")


def test_run_followups_command(cli_rig):
    cli, sent, clock = cli_rig
    cli.handle_line("script intent=interested followup=true")
    cli.handle_line("msg c1 下周再聊")
    assert "没有到期" in cli.handle_line("run_followups")
    clock.advance(3601)
    out = cli.handle_line("run_followups")
    assert "已发送" in out
    assert len(sent) == 2  # 确认回复 + 跟进触达


def test_unknown_command(cli_rig):
    cli, _, _ = cli_rig
    assert "未知命令" in cli.handle_line("foobar")


def test_script_rejected_outside_fake_mode(cli_rig):
    cli, _, _ = cli_rig
    cli._adapter = object()  # 非 FakeAdapter
    assert "不可用" in cli.handle_line("script intent=interested")
