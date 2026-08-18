"""执行层：约束 1/2/3 的最终强制点。

每个动作依次经过：
1. allowlist 校验——非 Action 枚举一律拒绝（未知动作、伪造工具调用）；
2. 状态门禁——escalated / closed 状态下静默（仅幂等确认对应动作）；
3. 滑动窗口限流——仅 reply 占用配额；
4. 统一发送函数——唯一写限流时间戳的地方，之后写审计。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from kapibala.audit import AuditLog
from kapibala.followup import Followup, FollowupQueue
from kapibala.rate_limiter import SlidingWindowRateLimiter
from kapibala.schemas import Action
from kapibala.state_machine import SessionState, StateMachine


@dataclass(frozen=True)
class ExecutionResult:
    executed: bool
    reason: str = ""


class Executor:
    """动作执行器。

    Args:
        state_machine: 确定性状态机（门禁与状态写入）。
        rate_limiter: 滑动窗口限流器。
        sink: 最终发送通道，签名 (customer_id, text) -> None。
        audit: 审计日志。
        followups: 跟进队列；不传则 schedule_followup 仅记审计。
    """

    def __init__(
        self,
        state_machine: StateMachine,
        rate_limiter: SlidingWindowRateLimiter,
        sink: Callable[[str, str], None],
        audit: AuditLog,
        followups: FollowupQueue | None = None,
    ) -> None:
        self._sm = state_machine
        self._limiter = rate_limiter
        self._sink = sink
        self._audit = audit
        self._followups = followups

    def execute(
        self,
        customer_id: str,
        action: object,
        *,
        reply_text: str | None = None,
        followup: Followup | None = None,
    ) -> ExecutionResult:
        # 1. allowlist 校验：只接受 Action 枚举，其余（字符串、伪造调用）一律拒绝
        if not isinstance(action, Action):
            self._audit.record(customer_id, "action_rejected", f"unknown_action: {action!r}")
            return ExecutionResult(False, "unknown_action")

        session = self._sm.get(customer_id).session

        # 2. 状态门禁：escalated 严格静默；closed 不再自动处理
        if session is SessionState.ESCALATED:
            if action is Action.ESCALATE_TO_HUMAN:
                # 状态机已确定性升级，动作作为幂等确认记账
                self._audit.record(customer_id, "escalate_confirmed")
                return ExecutionResult(True, "already_escalated")
            self._audit.record(customer_id, "action_silenced", action.value)
            return ExecutionResult(False, "escalated_silence")
        if session is SessionState.CLOSED:
            if action is Action.MARK_NOT_INTERESTED:
                self._audit.record(customer_id, "close_confirmed")
                return ExecutionResult(True, "already_closed")
            self._audit.record(customer_id, "action_silenced", action.value)
            return ExecutionResult(False, "closed")

        # 3/4. 动作分发
        if action is Action.REPLY:
            return self._execute_reply(customer_id, reply_text)
        if action is Action.SCHEDULE_FOLLOWUP:
            if self._followups is not None and followup is not None:
                self._followups.add(followup)
            self._audit.record(customer_id, "followup_scheduled")
            return ExecutionResult(True, "followup_scheduled")
        if action is Action.ESCALATE_TO_HUMAN:
            self._sm.force_escalate(customer_id)
            self._audit.record(customer_id, "escalated_to_human")
            return ExecutionResult(True, "escalated")
        # Action.MARK_NOT_INTERESTED
        self._sm.force_close(customer_id)
        self._audit.record(customer_id, "marked_not_interested")
        return ExecutionResult(True, "closed")

    def _execute_reply(self, customer_id: str, reply_text: str | None) -> ExecutionResult:
        if not reply_text:
            self._audit.record(customer_id, "action_rejected", "empty_reply")
            return ExecutionResult(False, "empty_reply")
        # 限流校验：被拒绝时静默不发送 + 写审计（可解释的安全降级）
        if not self._limiter.allow(customer_id):
            self._audit.record(customer_id, "reply_rate_limited")
            return ExecutionResult(False, "rate_limited")
        self._send(customer_id, reply_text)
        return ExecutionResult(True, "sent")

    def _send(self, customer_id: str, text: str) -> None:
        """统一发送函数：所有客户可见消息的唯一出口，唯一写限流时间戳的地方。"""
        self._limiter.record(customer_id)
        self._sink(customer_id, text)
        self._audit.record(customer_id, "message_sent", text)
