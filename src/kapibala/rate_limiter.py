"""Thread-safe sliding-window limiter with atomic send reservations."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class RateLimitReservation:
    """Opaque right to attempt one send for a customer."""

    customer_id: str
    token: int


@dataclass
class _Entry:
    token: int
    at: float
    pending: bool


class SlidingWindowRateLimiter:
    """每客户滑动窗口限流。

    窗口语义：发送时刻 t 之前 [t - window, t) 内的发送计入占用。
    即恰好 window 秒前的消息已滑出窗口（60s 整可发，59s 不可发）。
    """

    def __init__(
        self,
        window_seconds: float = 60.0,
        max_per_window: int = 1,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._window = window_seconds
        self._max = max_per_window
        self._clock = clock
        self._entries: dict[str, list[_Entry]] = {}
        self._lock = threading.Lock()
        self._next_token = 1

    def _prune_locked(self, customer_id: str, now: float) -> list[_Entry]:
        """Drop committed entries outside the window; pending sends stay reserved."""
        kept = [
            entry
            for entry in self._entries.get(customer_id, [])
            if entry.pending or now - entry.at < self._window
        ]
        self._entries[customer_id] = kept
        return kept

    def allow(self, customer_id: str) -> bool:
        """Read-only diagnostic check; sending code must use ``reserve``."""
        with self._lock:
            return (
                len(self._prune_locked(customer_id, self._clock())) < self._max
            )

    def retry_after(self, customer_id: str) -> float:
        """Return seconds until one send slot is expected to be available.

        This is advisory only. Callers must still use ``reserve`` because a
        concurrent sender may consume the slot before the delay expires.
        """
        with self._lock:
            now = self._clock()
            entries = self._prune_locked(customer_id, now)
            if len(entries) < self._max:
                return 0.0
            release_times = sorted(
                now + self._window if entry.pending else entry.at + self._window
                for entry in entries
            )
            required_releases = len(entries) - self._max + 1
            return max(0.0, release_times[required_releases - 1] - now)

    def record(self, customer_id: str) -> None:
        """Compatibility helper for tests that record an already-finished send."""
        with self._lock:
            now = self._clock()
            entries = self._prune_locked(customer_id, now)
            entries.append(_Entry(self._allocate_token_locked(), now, False))

    def reserve(self, customer_id: str) -> RateLimitReservation | None:
        """Atomically check quota and reserve one in-flight send."""
        with self._lock:
            now = self._clock()
            entries = self._prune_locked(customer_id, now)
            if len(entries) >= self._max:
                return None
            token = self._allocate_token_locked()
            entries.append(_Entry(token, now, True))
            return RateLimitReservation(customer_id, token)

    def commit(self, reservation: RateLimitReservation) -> None:
        """Commit a successful send at its completion time."""
        with self._lock:
            entry = self._find_pending_locked(reservation)
            entry.at = self._clock()
            entry.pending = False

    def cancel(self, reservation: RateLimitReservation) -> None:
        """Release quota after a confirmed transport failure."""
        with self._lock:
            entries = self._entries.get(reservation.customer_id, [])
            self._entries[reservation.customer_id] = [
                entry for entry in entries if entry.token != reservation.token
            ]

    def reset(self, customer_id: str) -> None:
        """Clear committed and pending quota entries for a new demo session."""
        with self._lock:
            self._entries.pop(customer_id, None)

    def _allocate_token_locked(self) -> int:
        token = self._next_token
        self._next_token += 1
        return token

    def _find_pending_locked(self, reservation: RateLimitReservation) -> _Entry:
        for entry in self._entries.get(reservation.customer_id, []):
            if entry.token == reservation.token and entry.pending:
                return entry
        raise ValueError("unknown or completed rate-limit reservation")
