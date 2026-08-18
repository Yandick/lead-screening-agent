"""策略层：动作映射真值表（plan2.md 第 3 节，M1 编码依据）。

状态为 active 时，按以下优先级逐条匹配，命中即执行：

| # | 条件 | 计数器操作 | 动作 |
|---|------|-----------|------|
| 1 | intent == rejected | 清零 | mark_not_interested → closed |
| 2 | confidence < 0.5（连续第 2 次） | — | escalate_to_human |
| 3 | confidence < 0.5（首次） | 冻结（不增不清） | reply（澄清反问） |
| 4 | dissatisfied == true | +1；≥2 则升级 | escalate_to_human 或 reply（安抚 + 回应其意图） |
| 5 | intent == off_topic | +1；≥2 则升级 | escalate_to_human 或 reply（礼貌拉回话题） |
| 6 | intent == interested 且 followup_requested | 清零 | reply（确认）+ schedule_followup |
| 7 | intent == interested | 清零 | reply（推进转化） |
| 8 | intent == needs_info | 清零 | reply（解答问题） |
| 9 | intent == other | 清零 | reply（澄清式回应） |

补充规则：

- rejected 优先级最高：客户明确拒绝时，即使 dissatisfied == true 也直接
  关闭会话，不再安抚或升级；
- 规则 4/5 中"≥2 升级"沿用共享连续计数器（off_topic 与 dissatisfied
  混计达 2 次升级，出现其他情况重置）；
- 低置信计数独立于异常计数器：连续两次 confidence < 0.5 转人工；
- 状态 escalated：执行层无条件静默，仅 reactivate 命令恢复 active；
- 状态 closed：不再自动处理。
"""

from __future__ import annotations

from dataclasses import dataclass

from kapibala.schemas import (
    LOW_CONFIDENCE_THRESHOLD,
    Action,
    Estimation,
    Intent,
    ReplyKind,
)
from kapibala.state_machine import Transition


@dataclass(frozen=True)
class PolicyDecision:
    """策略层输出：一组合法动作 + 回复用途（供生成模块使用）。"""

    actions: tuple[Action, ...]
    reply_kind: ReplyKind | None = None


def decide(est: Estimation, transition: Transition) -> PolicyDecision:
    """按模块文档字符串中的真值表逐条匹配，命中即返回。"""
    # 规则 1：rejected 优先级最高，即使 dissatisfied 也直接关闭
    if transition.closed_now:
        return PolicyDecision(actions=(Action.MARK_NOT_INTERESTED,))

    # 规则 2/4/5 的"≥2 升级"分支：状态机已确定性转人工
    if transition.escalated_now:
        return PolicyDecision(actions=(Action.ESCALATE_TO_HUMAN,))

    # 规则 3：首次低置信，澄清反问
    if est.confidence < LOW_CONFIDENCE_THRESHOLD:
        return PolicyDecision(actions=(Action.REPLY,), reply_kind=ReplyKind.CLARIFY)

    # 规则 4：不满但未达升级阈值，安抚 + 回应其意图
    if est.dissatisfied:
        return PolicyDecision(actions=(Action.REPLY,), reply_kind=ReplyKind.SOOTHE)

    # 规则 5：答非所问，礼貌拉回话题
    if est.intent is Intent.OFF_TOPIC:
        return PolicyDecision(actions=(Action.REPLY,), reply_kind=ReplyKind.REDIRECT)

    # 规则 6：有兴趣且要求稍后跟进
    if est.intent is Intent.INTERESTED and est.followup_requested:
        return PolicyDecision(
            actions=(Action.REPLY, Action.SCHEDULE_FOLLOWUP),
            reply_kind=ReplyKind.CONFIRM_FOLLOWUP,
        )

    # 规则 7/8/9
    if est.intent is Intent.INTERESTED:
        return PolicyDecision(actions=(Action.REPLY,), reply_kind=ReplyKind.PITCH)
    if est.intent is Intent.NEEDS_INFO:
        return PolicyDecision(actions=(Action.REPLY,), reply_kind=ReplyKind.ANSWER)
    return PolicyDecision(actions=(Action.REPLY,), reply_kind=ReplyKind.GENERIC)
