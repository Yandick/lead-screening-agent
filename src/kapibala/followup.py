"""Trusted-operator follow-up records and the in-memory queue."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from numbers import Real


class FollowupValidationError(ValueError):
    """Raised when a trusted operator submits an invalid follow-up marker."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Followup:
    customer_id: str
    due_at: float  # 到期时间戳（与限流器同一时钟域）
    context: str = ""  # 跟进上下文（如客户最后一句话）

    @classmethod
    def from_operator(
        cls,
        customer_id: object,
        delay_seconds: object,
        context: object,
        *,
        clock: Callable[[], float],
    ) -> Followup:
        """Validate a trusted relative due time and build a normalized marker."""
        if not isinstance(customer_id, str) or not customer_id.strip():
            raise FollowupValidationError("invalid_customer_id")
        if (
            isinstance(delay_seconds, bool)
            or not isinstance(delay_seconds, Real)
            or not math.isfinite(float(delay_seconds))
            or delay_seconds < 0
        ):
            raise FollowupValidationError("invalid_delay_seconds")
        if not isinstance(context, str):
            raise FollowupValidationError("invalid_context")

        now = clock()
        if not math.isfinite(now):
            raise FollowupValidationError("invalid_clock")
        due_at = now + float(delay_seconds)
        if not math.isfinite(due_at):
            raise FollowupValidationError("invalid_delay_seconds")
        return cls(
            customer_id=customer_id.strip(),
            due_at=due_at,
            context=context.strip(),
        )


@dataclass
class FollowupQueue:
    _items: list[Followup] = field(default_factory=list)

    def add(self, followup: Followup) -> None:
        self._items.append(followup)

    def pop_due(self, now: float) -> list[Followup]:
        """取出并移除所有到期跟进。"""
        due = [f for f in self._items if f.due_at <= now]
        self._items = [f for f in self._items if f.due_at > now]
        return due

    def snapshot(self, customer_id: str | None = None) -> tuple[Followup, ...]:
        """Return an immutable insertion-ordered view for operator inspection."""
        items = tuple(self._items)
        if customer_id is None:
            return items
        return tuple(item for item in items if item.customer_id == customer_id)

    def __len__(self) -> int:
        return len(self._items)
