"""终端交互 CLI。

命令：
  msg <customer_id> <text>   处理一条客户消息，显示判定摘要、状态变化、动作
  reactivate <customer_id>   人工恢复 active
  show_state <customer_id>   查看状态机与计数器
  run_followups              触发到期跟进
  script <k=v ...>           （仅 FakeAdapter）预排下一次 LLM 判定，便于演示
  reset <customer_id>        清空该客户状态（演示用）
  help / quit
"""

from __future__ import annotations

import os
import time

from kapibala.agent import DEFAULT_FOLLOWUP_DELAY, ProcessResult, ScreeningAgent
from kapibala.adapters.base import LLMError
from kapibala.adapters.fake import FakeAdapter
from kapibala.audit import AuditLog
from kapibala.executor import Executor
from kapibala.followup import FollowupQueue
from kapibala.rate_limiter import SlidingWindowRateLimiter
from kapibala.reply_generator import TemplateReplyGenerator
from kapibala.schemas import Estimation, Intent
from kapibala.state_machine import StateMachine

HELP_TEXT = """可用命令：
  msg <customer_id> <text>   发送一条客户消息（连发会聚合，静默几秒后统一处理）
  reactivate <customer_id>   人工恢复 active
  show_state <customer_id>   查看状态机与计数器
  run_followups              触发到期跟进
  script <k=v ...>           预排下一次 LLM 判定（fake 模式），如：
                             script intent=off_topic dissatisfied=true
                             script error
  reset <customer_id>        清空该客户状态
  help                       显示本帮助
  quit                       退出"""


class CLI:
    """可测试的命令处理器：handle_line(line) -> 输出文本。

    传入 debouncer 时，msg 命令进入防抖聚合（连发合并为一批处理，结果异步
    经 printer 输出）；不传则同步处理（测试与脚本场景）。
    """

    def __init__(
        self,
        agent: ScreeningAgent,
        sm: StateMachine,
        adapter,
        audit: AuditLog,
        debouncer=None,
        debounce_window: float = 3.0,
        printer=print,
    ) -> None:
        self._agent = agent
        self._sm = sm
        self._adapter = adapter
        self._audit = audit
        self._debouncer = debouncer
        self._debounce_window = debounce_window
        self._printer = printer
        self._timers: dict = {}

    def handle_line(self, line: str) -> str:
        parts = line.strip().split(maxsplit=2)
        if not parts:
            return ""
        cmd, args = parts[0].lower(), parts[1:]
        if cmd == "msg" and len(args) == 2:
            cid, text = args[0], args[1]
            if self._debouncer is None:
                return self._format_result(cid, self._agent.handle_message(cid, text))
            count = self._debouncer.feed(cid, text)
            self._schedule_flush(cid)
            return (
                f"[{cid}] 已接收（{count} 条待聚合，静默 "
                f"{self._debounce_window:.0f}s 后统一处理）。"
            )
        if cmd == "reactivate" and len(args) >= 1:
            self._sm.reactivate(args[0])
            self._audit.record(args[0], "reactivated", "by human operator")
            return f"[{args[0]}] 已人工重新激活，恢复 active。"
        if cmd == "show_state" and len(args) >= 1:
            return self._format_state(args[0])
        if cmd == "run_followups":
            outcomes = self._agent.run_followups()
            if not outcomes:
                return "没有到期的跟进。"
            lines = []
            for followup, res in outcomes:
                status = "已发送" if res.executed else f"未发送（{res.reason}）"
                lines.append(f"[{followup.customer_id}] 跟进触达：{status}")
            return "\n".join(lines)
        if cmd == "script" and args:
            return self._handle_script(" ".join(args))
        if cmd == "reset" and len(args) >= 1:
            self._sm.reset(args[0])
            return f"[{args[0]}] 状态已清空。"
        if cmd == "help":
            return HELP_TEXT
        if cmd in ("quit", "exit"):
            raise SystemExit(0)
        return "未知命令，输入 help 查看用法。"

    def _schedule_flush(self, customer_id: str) -> None:
        import threading

        old = self._timers.pop(customer_id, None)
        if old is not None:
            old.cancel()
        timer = threading.Timer(
            self._debounce_window, self._flush_and_print, args=(customer_id,)
        )
        timer.daemon = True
        timer.start()
        self._timers[customer_id] = timer

    def _flush_and_print(self, customer_id: str) -> None:
        self._timers.pop(customer_id, None)
        result = self._debouncer.flush_customer(customer_id)
        if result is not None:
            self._printer(self._format_result(result[0], result[1]))

    def drain(self) -> None:
        """退出前立即处理所有待聚合批次。"""
        for timer in self._timers.values():
            timer.cancel()
        self._timers.clear()
        if self._debouncer is None:
            return
        for cid in self._debouncer.pending_customers():
            self._flush_and_print(cid)

    def _handle_script(self, spec: str) -> str:
        if not isinstance(self._adapter, FakeAdapter):
            return "当前不是 fake 模式，script 命令不可用。"
        if spec.strip() == "error":
            self._adapter.script(LLMError("scripted error"))
            return "已预排：下一次估计抛出 LLMError（演示 fail-closed）。"
        kv = dict(pair.split("=", 1) for pair in spec.split() if "=" in pair)
        try:
            unknown = set(kv) - {"intent", "dissatisfied"}
            if unknown:
                raise ValueError(f"未知字段：{', '.join(sorted(unknown))}")
            est = Estimation(
                intent=Intent(kv.get("intent", "other")),
                dissatisfied=kv.get("dissatisfied", "false").lower() == "true",
            )
        except ValueError as exc:
            return f"script 参数非法：{exc}"
        self._adapter.script(est)
        return f"已预排下一次判定：{self._format_estimation(est)}"

    def _format_result(self, cid: str, result: ProcessResult) -> str:
        if result.note == "silent_escalated":
            return f"[{cid}] 已转人工，保持静默（消息未作任何处理）。"
        if result.note == "silent_closed":
            return f"[{cid}] 会话已关闭，不再自动处理。"
        if result.note == "fail_closed":
            return f"[{cid}] LLM 调用失败，已进入安全的待处理状态（未发送任何消息）。"
        lines = [f"[{cid}] 判定：{self._format_estimation(result.estimation)}"]
        t = result.transition
        if t.escalated_now:
            lines.append("状态变化：active -> escalated（转人工，进入静默）")
        elif t.closed_now:
            lines.append("状态变化：active -> closed（会话结束）")
        assert result.decision is not None
        action_names = ", ".join(a.value for a in result.decision.actions)
        lines.append(f"动作：{action_names}")
        if result.reply_text is not None:
            lines.append(f"回复草稿：{result.reply_text}")
        for res in result.executions:
            if not res.executed:
                lines.append(f"执行结果：被拦截（{res.reason}）")
        return "\n".join(lines)

    def _format_state(self, cid: str) -> str:
        st = self._sm.get(cid)
        lines = [
            f"[{cid}] session={st.session.value} "
            f"anomaly_count={st.anomaly_count}"
        ]
        if st.last_estimation is not None:
            lines.append(f"最近判定：{self._format_estimation(st.last_estimation)}")
        return "\n".join(lines)

    @staticmethod
    def _format_estimation(est: Estimation | None) -> str:
        if est is None:
            return "（无）"
        return f"intent={est.intent.value} dissatisfied={est.dissatisfied}"


def build_cli(sink=None) -> CLI:
    """组装默认 Agent：无 GEMINI_API_KEY 时使用 FakeAdapter（离线 demo）。"""
    clock = time.monotonic
    sm = StateMachine()
    limiter = SlidingWindowRateLimiter(clock=clock)
    audit = AuditLog(clock=clock)
    followups = FollowupQueue()
    if sink is None:
        sink = lambda cid, text: print(f"\n>>> 发送给 [{cid}]：{text}\n")  # noqa: E731
    executor = Executor(sm, limiter, sink, audit, followups)

    if os.environ.get("GEMINI_API_KEY"):
        from kapibala.adapters.gemini import GeminiAdapter
        from kapibala.gemini_reply import GeminiReplyGenerator

        adapter: object = GeminiAdapter()
        generator = GeminiReplyGenerator(adapter)
        mode = f"gemini 模式（model={adapter.model}，LLM 生成回复）"
    else:
        adapter = FakeAdapter()
        generator = TemplateReplyGenerator()
        mode = "fake 模式（用 script 命令预排 LLM 判定）"
    followup_delay = float(os.environ.get("FOLLOWUP_DELAY_SECONDS", DEFAULT_FOLLOWUP_DELAY))
    agent = ScreeningAgent(
        adapter, generator, executor, sm, audit, followups,
        clock=clock, followup_delay=followup_delay,
    )
    from kapibala.debounce import MessageDebouncer

    debounce_window = float(os.environ.get("DEBOUNCE_SECONDS", "3"))
    debouncer = MessageDebouncer(agent.handle_message, clock=clock, window_seconds=debounce_window)
    return CLI(agent, sm, adapter, audit, debouncer=debouncer, debounce_window=debounce_window), mode


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv()
    cli, mode = build_cli()
    print(f"获客初筛 Agent（{mode}）")
    print(HELP_TEXT)
    while True:
        try:
            line = input("\nkapibala> ")
        except (EOFError, KeyboardInterrupt):
            print("\n再见。")
            break
        try:
            output = cli.handle_line(line)
        except SystemExit:
            cli.drain()
            print("再见。")
            break
        if output:
            print(output)


if __name__ == "__main__":
    main()
