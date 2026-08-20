"""Structured context passed to classifiers and reply generators."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from kapibala.runtime import ConversationTurn
from kapibala.schemas import Estimation, Intent, ReplyKind


DEFAULT_COMPANY_CONTEXT = (
    "Kapibala is an AI startup. No other company facts have been verified."
)
DEFAULT_PRODUCT_CONTEXT = (
    "No product capabilities, pricing, integrations, availability, case studies, "
    "or commercial terms have been verified."
)


@dataclass(frozen=True)
class BusinessContext:
    """Trusted business facts available to the reply generator."""

    company: str = DEFAULT_COMPANY_CONTEXT
    product: str = DEFAULT_PRODUCT_CONTEXT

    def __post_init__(self) -> None:
        if not isinstance(self.company, str) or not self.company.strip():
            object.__setattr__(self, "company", DEFAULT_COMPANY_CONTEXT)
        if not isinstance(self.product, str) or not self.product.strip():
            object.__setattr__(self, "product", DEFAULT_PRODUCT_CONTEXT)


#: 不可信对话数据在发给模型的 JSON payload 中的唯一外层 key。
#: system prompt 与测试引用同一常量，保证三处永不脱节。
UNTRUSTED_PAYLOAD_KEY = "untrusted_conversation_data"


def serialize_untrusted_payload(
    history: tuple[ConversationTurn, ...], message: str
) -> str:
    """不可信对话数据（最近历史 + 当前消息）的唯一序列化出口。

    分类与回复生成共用同一格式：单层包裹，`untrusted_` 前缀只出现在边界上，
    包裹内的所有内容一律视为不可信数据。
    """
    payload = {
        UNTRUSTED_PAYLOAD_KEY: {
            "recent_history": [
                {"role": turn.role.value, "content": turn.content}
                for turn in history
            ],
            "current_message": message,
        }
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


@dataclass(frozen=True)
class ClassificationRequest:
    """Current untrusted message and its bounded, customer-local history."""

    message: str
    history: tuple[ConversationTurn, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ReplyRequest:
    """Complete structured input for one reply-draft request."""

    business: BusinessContext
    history: tuple[ConversationTurn, ...]
    message: str
    intent: Intent
    dissatisfied: bool
    reply_kind: ReplyKind

    @classmethod
    def from_estimation(
        cls,
        *,
        business: BusinessContext,
        history: tuple[ConversationTurn, ...],
        message: str,
        estimation: Estimation,
        reply_kind: ReplyKind,
    ) -> ReplyRequest:
        return cls(
            business=business,
            history=history,
            message=message,
            intent=estimation.intent,
            dissatisfied=estimation.dissatisfied,
            reply_kind=reply_kind,
        )
