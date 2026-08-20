"""审计日志：内存实现，进程重启清空（符合 demo 范围）。"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class AuditEvent:
    customer_id: str
    event: str
    detail: str = ""
    at: float = 0.0


@dataclass
class AuditLog:
    """append-only 审计日志。"""

    clock: Callable[[], float] = time.monotonic
    _events: list[AuditEvent] = field(default_factory=list)

    def record(self, customer_id: str, event: str, detail: str = "") -> None:
        self._events.append(
            AuditEvent(customer_id=customer_id, event=event, detail=detail, at=self.clock())
        )

    @property
    def events(self) -> tuple[AuditEvent, ...]:
        return tuple(self._events)

    def events_for(self, customer_id: str) -> tuple[AuditEvent, ...]:
        return tuple(e for e in self._events if e.customer_id == customer_id)

    def reset(self, customer_id: str) -> None:
        """Remove one customer's audit trail when starting a new demo session."""
        self._events = [
            event for event in self._events if event.customer_id != customer_id
        ]
