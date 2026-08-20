"""策略层：动作映射真值表（plan2.md 第 3 节，M1 编码依据）。

状态为 active 时，按以下优先级逐条匹配，命中即执行：

| # | 条件 | 计数器操作 | 动作 |
|---|------|-----------|------|
| 1 | transition.closed_now | 已关闭 | mark_not_interested |
| 2 | transition.escalated_now | 已升级 | escalate_to_human |
| 3 | followup_requested == true | 已由状态机更新 | schedule_followup（本轮不回复） |
| 4 | dissatisfied == true | 已由状态机 +1 | reply（安抚 + 回应其意图） |
| 5 | intent == off_topic | 已由状态机 +1 | reply（礼貌拉回话题） |
| 6 | intent == interested | 已清零 | reply（推进转化） |
| 7 | intent == needs_info | 已清零 | reply（解答问题） |
| 8 | intent == other | 已清零 | reply（澄清式回应） |

补充规则：

- 连续异常阈值优先于 rejected：若 rejected+dissatisfied 是连续第二次异常，
  必须先转人工；单次愤怒拒绝仍直接关闭；
- 状态机中的"≥2 升级"沿用共享连续计数器（off_topic 与 dissatisfied
  混计达 2 次升级，出现其他情况重置）；
- 状态 escalated：执行层无条件静默，仅 reactivate 命令恢复 active；
- 状态 closed：不再自动处理。
"""

from __future__ import annotations

from dataclasses import dataclass

from kapibala.schemas import Action, Estimation, Intent, ReplyKind
from kapibala.state_machine import Transition


@dataclass(frozen=True)
class PolicyDecision:
    """策略层输出：一组合法动作 + 回复用途（供生成模块使用）。"""

    actions: tuple[Action, ...]
    reply_kind: ReplyKind | None = None


def decide(est: Estimation, transition: Transition) -> PolicyDecision:
    """按模块文档字符串中的真值表逐条匹配，命中即返回。"""
    # 规则 1：状态机未触发异常升级时，rejected 进入关闭分支
    if transition.closed_now:
        return PolicyDecision(actions=(Action.MARK_NOT_INTERESTED,))

    # 规则 2：共享异常计数达到阈值，状态机已确定性转人工
    if transition.escalated_now:
        return PolicyDecision(actions=(Action.ESCALATE_TO_HUMAN,))

    # 明确要求稍后联系时只做标记，本轮不得同时 reply。
    if est.followup_requested:
        return PolicyDecision(actions=(Action.SCHEDULE_FOLLOWUP,))

    # 规则 4：不满但未达升级阈值，安抚 + 回应其意图
    if est.dissatisfied:
        return PolicyDecision(actions=(Action.REPLY,), reply_kind=ReplyKind.SOOTHE)

    # 规则 5：答非所问，礼貌拉回话题
    if est.intent is Intent.OFF_TOPIC:
        return PolicyDecision(actions=(Action.REPLY,), reply_kind=ReplyKind.REDIRECT)

    # 规则 6/7/8
    if est.intent is Intent.INTERESTED:
        return PolicyDecision(actions=(Action.REPLY,), reply_kind=ReplyKind.PITCH)
    if est.intent is Intent.NEEDS_INFO:
        return PolicyDecision(actions=(Action.REPLY,), reply_kind=ReplyKind.ANSWER)
    return PolicyDecision(actions=(Action.REPLY,), reply_kind=ReplyKind.GENERIC)
