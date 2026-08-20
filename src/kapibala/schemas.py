"""LLM 结构化状态估计的数据结构。

LLM 输出只能映射到本模块定义的数据结构，不能直接调用任何函数或工具。
字段语义见 plan2.md 第 2.1 节。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Intent(str, Enum):
    """五类客户意图（题目要求的枚举，API 层即限定取值）。"""

    INTERESTED = "interested"
    NEEDS_INFO = "needs_info"
    REJECTED = "rejected"
    OFF_TOPIC = "off_topic"
    OTHER = "other"


class Action(str, Enum):
    """允许执行的动作全集（allowlist，除此之外一律拒绝）。"""

    REPLY = "reply"
    SCHEDULE_FOLLOWUP = "schedule_followup"
    ESCALATE_TO_HUMAN = "escalate_to_human"
    MARK_NOT_INTERESTED = "mark_not_interested"


class ReplyKind(str, Enum):
    """回复用途，供回复生成模块组织生成提示词。"""

    SOOTHE = "soothe"  # 安抚 + 回应其意图
    REDIRECT = "redirect"  # 礼貌拉回话题
    CONFIRM_FOLLOWUP = "confirm_followup"  # 确认稍后跟进
    PITCH = "pitch"  # 推进转化
    ANSWER = "answer"  # 解答问题
    GENERIC = "generic"  # 澄清式回应


@dataclass(frozen=True)
class Estimation:
    """两次独立 LLM 分类均通过校验后合并的业务结果。

    - intent: 五类意图之一；
    - dissatisfied: 正交情绪信号，不得从 intent 推导；
    """

    intent: Intent
    dissatisfied: bool = False
