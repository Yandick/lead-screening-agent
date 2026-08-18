"""LLM 结构化状态估计的数据结构。

LLM 输出只能映射到本模块定义的数据结构，不能直接调用任何函数或工具。
字段语义见 plan2.md 第 2.1 节。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Intent(str, Enum):
    """五类客户意图（题目要求的枚举，API 层即限定取值）。"""

    INTERESTED = "interested"
    NEEDS_INFO = "needs_info"
    REJECTED = "rejected"
    OFF_TOPIC = "off_topic"
    OTHER = "other"


@dataclass(frozen=True)
class Estimation:
    """一次 LLM 结构化状态估计的结果。

    - intent: 五类意图之一；
    - dissatisfied: 正交情绪信号，不得从 intent 推导；
    - followup_requested: 客户是否明确表示"稍后再联系/现在忙/下周再聊"；
    - confidence: 0~1，参与策略（低置信触发澄清/升级）；
    - reason: 仅内部审计，不向客户展示。
    """

    intent: Intent
    dissatisfied: bool = False
    followup_requested: bool = False
    confidence: float = 1.0
    reason: str = field(default="")
