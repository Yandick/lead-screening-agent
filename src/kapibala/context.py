"""Structured context passed to classifiers and reply generators."""

from __future__ import annotations

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
