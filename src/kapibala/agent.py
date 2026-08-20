"""管道编排：把各层串成完整的处理链路。

客户消息
  -> 状态门禁前置（escalated/closed 直接静默，连 LLM 都不调用）
  -> LLM 结构化状态估计（LLMError -> fail-closed：不发消息、记审计）
  -> 状态机 apply -> 策略层 decide
  -> 回复生成 -> output_guard -> 执行层（allowlist/门禁/限流/统一发送）
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from kapibala import output_guard
from kapibala.adapters.base import LLMAdapter, LLMError
from kapibala.audit import AuditLog
from kapibala.executor import ExecutionResult, Executor
from kapibala.followup import Followup, FollowupQueue
from kapibala.policy import PolicyDecision, decide
from kapibala.reply_generator import FOLLOWUP_TEMPLATE, ReplyGenerator
from kapibala.runtime import (
    ConversationStore,
    InputValidationError,
    RuntimeConfig,
    RuntimeInput,
)
from kapibala.schemas import Action, Estimation, ReplyKind
from kapibala.state_machine import SessionState, StateMachine, Transition

#: 默认跟进延迟（秒）
DEFAULT_FOLLOWUP_DELAY = 3600.0


@dataclass
class ProcessResult:
    """一条客户消息的处理结果摘要，供 CLI 展示。"""

    note: str  # "ok" | "invalid_input" | "silent_*" | "fail_closed"
    estimation: Estimation | None = None
    transition: Transition = field(default_factory=Transition)
    decision: PolicyDecision | None = None
    executions: list[ExecutionResult] = field(default_factory=list)
    reply_text: str | None = None  # 经 output_guard 检查后的回复（发送或被限流的）
    input_error: str | None = None


class ScreeningAgent:
    """获客初筛 Agent 主流程。"""

    def __init__(
        self,
        adapter: LLMAdapter,
        generator: ReplyGenerator,
        executor: Executor,
        state_machine: StateMachine,
        audit: AuditLog,
        followups: FollowupQueue,
        clock: Callable[[], float] = time.monotonic,
        followup_delay: float = DEFAULT_FOLLOWUP_DELAY,
        runtime_config: RuntimeConfig | None = None,
        conversation_store: ConversationStore | None = None,
    ) -> None:
        self._adapter = adapter
        self._generator = generator
        self._executor = executor
        self._sm = state_machine
        self._audit = audit
        self._followups = followups
        self._clock = clock
        self._followup_delay = followup_delay
        self._runtime_config = runtime_config or RuntimeConfig()
        self._history = conversation_store or ConversationStore(
            self._runtime_config.max_history_turns
        )

    @property
    def conversation_store(self) -> ConversationStore:
        """Read-only access to the injected store for later context consumers."""
        return self._history

    def handle_message(self, customer_id: object, text: object) -> ProcessResult:
        try:
            runtime_input = RuntimeInput.validate(
                customer_id, text, self._runtime_config
            )
        except InputValidationError as exc:
            return ProcessResult(note="invalid_input", input_error=exc.reason)

        customer_id = runtime_input.customer_id
        text = runtime_input.message

        # 前置门禁：escalated / closed 状态下连 LLM 都不调用，直接静默
        session = self._sm.get(customer_id).session
        if session is SessionState.ESCALATED:
            self._audit.record(customer_id, "message_ignored", "escalated")
            return ProcessResult(note="silent_escalated")
        if session is SessionState.CLOSED:
            self._audit.record(customer_id, "message_ignored", "closed")
            return ProcessResult(note="silent_closed")

        # 只记录已通过输入检查且在接收时属于 active 会话的入站消息。
        self._history.append_customer(customer_id, text)

        # LLM 结构化状态估计；任何失败 fail-closed：不发客户可见消息
        try:
            est = self._adapter.estimate(text)
        except LLMError as exc:
            self._audit.record(customer_id, "llm_error", str(exc))
            return ProcessResult(note="fail_closed")

        transition = self._sm.apply(customer_id, est)
        decision = decide(est, transition)
        result = ProcessResult(note="ok", estimation=est, transition=transition, decision=decision)

        for action in decision.actions:
            if action is Action.REPLY:
                reply_text = self._prepare_reply(customer_id, decision.reply_kind, text, est)
                result.reply_text = reply_text
                execution = self._executor.execute(
                    customer_id, Action.REPLY, reply_text=reply_text
                )
                result.executions.append(execution)
                if execution.executed and execution.reason == "sent":
                    self._history.append_assistant(customer_id, reply_text)
            elif action is Action.SCHEDULE_FOLLOWUP:
                followup = Followup(
                    customer_id=customer_id,
                    due_at=self._clock() + self._followup_delay,
                    context=text,
                )
                result.executions.append(
                    self._executor.execute(
                        customer_id, Action.SCHEDULE_FOLLOWUP, followup=followup
                    )
                )
            else:
                result.executions.append(self._executor.execute(customer_id, action))
        return result

    def run_followups(self) -> list[tuple[Followup, ExecutionResult]]:
        """触发到期跟进：生成 -> output_guard -> 状态门禁 -> 限流 -> 统一发送。"""
        outcomes: list[tuple[Followup, ExecutionResult]] = []
        for followup in self._followups.pop_due(self._clock()):
            guarded = output_guard.sanitize(FOLLOWUP_TEMPLATE)
            if not guarded.passed:
                self._audit.record(followup.customer_id, "guard_replaced", guarded.reason)
            execution = self._executor.execute(
                followup.customer_id, Action.REPLY, reply_text=guarded.text
            )
            outcomes.append((followup, execution))
            if execution.executed and execution.reason == "sent":
                self._history.append_assistant(followup.customer_id, guarded.text)
        return outcomes

    def _prepare_reply(
        self, customer_id: str, kind: ReplyKind | None, message: str, est: Estimation
    ) -> str:
        """生成回复草稿并过 output_guard；命中防护则替换为安全回复并记审计。"""
        draft = self._generator.generate(kind or ReplyKind.GENERIC, message, est)
        guarded = output_guard.sanitize(draft)
        if not guarded.passed:
            self._audit.record(customer_id, "guard_replaced", guarded.reason)
        return guarded.text
