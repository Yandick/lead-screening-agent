"""Follow-up records and the in-memory queue."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Followup:
    customer_id: str
    due_at: float  # 到期时间戳（与限流器同一时钟域）
    context: str = ""  # 跟进上下文（如客户最后一句话）

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

    def reset(self, customer_id: str) -> None:
        """Remove every pending marker for one customer."""
        self._items = [
            item for item in self._items if item.customer_id != customer_id
        ]

    def __len__(self) -> int:
        return len(self._items)
