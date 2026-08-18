"""确定性客户状态机。

约束 2 的强制层：连续 2 次 off_topic / dissatisfied（共享计数器）或
连续 2 次低置信，必转人工——不依赖 LLM 单次输出，任何情况下都生效。
LLM 输出（Estimation）只是本状态机的输入，状态转移规则全部写死在本模块。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from kapibala.schemas import LOW_CONFIDENCE_THRESHOLD, Estimation, Intent

#: 连续异常/低置信达到该次数即转人工（题目硬性约束：2 次）
ESCALATION_THRESHOLD = 2


class SessionState(Enum):
    ACTIVE = "active"
    ESCALATED = "escalated"
    CLOSED = "closed"


@dataclass
class CustomerState:
    """单个客户的连续状态。各客户状态隔离，互不影响。"""

    session: SessionState = SessionState.ACTIVE
    anomaly_count: int = 0  # off_topic 与 dissatisfied 共享的连续计数器
    low_confidence_count: int = 0  # 连续低置信计数（plan2 规则 2/3）
    last_estimation: Estimation | None = None


@dataclass(frozen=True)
class Transition:
    """一次 apply 引发的状态跃迁标记，供策略层选动作。"""

    escalated_now: bool = False
    closed_now: bool = False


class StateMachine:
    """多客户状态机。

    转移规则（与 plan2 第 3 节真值表的计数器列一一对应）：
    - rejected：计数器清零，直接进入 closed；
    - confidence < 阈值：低置信计数 +1（异常计数器冻结），连续 2 次转人工；
    - dissatisfied 或 off_topic：异常计数器 +1，达 2 次转人工；
    - 其他任何情况：异常计数器清零，低置信计数清零。
    """

    def __init__(self) -> None:
        self._states: dict[str, CustomerState] = {}

    def get(self, customer_id: str) -> CustomerState:
        """取客户状态，不存在则创建默认 active 状态。"""
        return self._states.setdefault(customer_id, CustomerState())

    def apply(self, customer_id: str, est: Estimation) -> Transition:
        """按估计结果推进状态机。非 active 状态下调用为空操作。"""
        st = self.get(customer_id)
        if st.session is not SessionState.ACTIVE:
            return Transition()
        st.last_estimation = est

        if est.intent is Intent.REJECTED:
            st.anomaly_count = 0
            st.low_confidence_count = 0
            st.session = SessionState.CLOSED
            return Transition(closed_now=True)

        if est.confidence < LOW_CONFIDENCE_THRESHOLD:
            st.low_confidence_count += 1
            # 异常计数器冻结：不增不清
            if st.low_confidence_count >= ESCALATION_THRESHOLD:
                st.session = SessionState.ESCALATED
                return Transition(escalated_now=True)
            return Transition()

        st.low_confidence_count = 0

        if est.dissatisfied or est.intent is Intent.OFF_TOPIC:
            st.anomaly_count += 1
            if st.anomaly_count >= ESCALATION_THRESHOLD:
                st.session = SessionState.ESCALATED
                return Transition(escalated_now=True)
            return Transition()

        st.anomaly_count = 0
        return Transition()

    def reactivate(self, customer_id: str) -> None:
        """人工重新激活：恢复 active 并清零计数器。"""
        st = self.get(customer_id)
        st.session = SessionState.ACTIVE
        st.anomaly_count = 0
        st.low_confidence_count = 0

    def force_escalate(self, customer_id: str) -> None:
        """执行层执行 escalate_to_human 时调用。"""
        self.get(customer_id).session = SessionState.ESCALATED

    def force_close(self, customer_id: str) -> None:
        """执行层执行 mark_not_interested 时调用。"""
        self.get(customer_id).session = SessionState.CLOSED

    def reset(self, customer_id: str) -> None:
        """清空该客户状态（演示用）。"""
        self._states.pop(customer_id, None)
