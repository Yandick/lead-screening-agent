"""消息防抖聚合层。

IM 场景下客户常把一句话拆成多条连发（"在吗"→"你们那个系统"→"多少钱"）。
逐条处理会导致：回复的是最不重要的第一条、碎片被重复计数甚至误触发升级。
本层把同一客户在静默窗口内的连发消息合并为一批，只走一次完整管道——
与 60 秒限流天然契合（一个窗口一条回复，回复的是整段意思而非第一个碎片）。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class BufferSubmission:
    """Result of accepting one inbound message at the reply-interval boundary."""

    buffered: bool
    pending_count: int
    due_in_seconds: float
    result: object | None = None


class ReplyIntervalBuffer:
    """Aggregate inbound messages while a customer's send quota is cooling down.

    The first message is processed immediately when no outbound cooldown exists.
    Messages received during the cooldown are joined in arrival order and passed
    through the full agent pipeline once the send slot is expected to reopen.
    """

    def __init__(
        self,
        on_flush: Callable[[str, str], object],
        cooldown_remaining: Callable[[str], float],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._on_flush = on_flush
        self._cooldown_remaining = cooldown_remaining
        self._clock = clock
        self._pending: dict[str, list[str]] = {}
        self._due_at: dict[str, float] = {}
        self._lock = threading.Lock()

    def submit(
        self, customer_id: str, text: str, *, force: bool = False
    ) -> BufferSubmission:
        """Process now or append to the current fixed cooldown batch.

        ``force`` bypasses the cooldown but still combines any already-buffered
        messages. It is reserved for code-level priority gates such as an
        explicit request for a human.
        """
        with self._lock:
            waiting = self._pending.get(customer_id)
            remaining = max(0.0, self._cooldown_remaining(customer_id))
            if not force and (waiting is not None or remaining > 0.0):
                messages = self._pending.setdefault(customer_id, [])
                messages.append(text)
                if customer_id not in self._due_at:
                    self._due_at[customer_id] = self._clock() + remaining
                return BufferSubmission(
                    buffered=True,
                    pending_count=len(messages),
                    due_in_seconds=self._due_in_locked(customer_id),
                )

            messages = self._pending.pop(customer_id, [])
            self._due_at.pop(customer_id, None)
            messages.append(text)

        combined = "\n".join(messages)
        return BufferSubmission(
            buffered=False,
            pending_count=0,
            due_in_seconds=0.0,
            result=self._on_flush(customer_id, combined),
        )

    def flush_customer(self, customer_id: str) -> tuple[str, object] | None:
        """Flush one due batch, or move its due time if cooldown was extended."""
        with self._lock:
            messages = self._pending.get(customer_id)
            if not messages:
                return None
            remaining = max(0.0, self._cooldown_remaining(customer_id))
            if remaining > 0.0:
                self._due_at[customer_id] = self._clock() + remaining
                return None
            messages = self._pending.pop(customer_id)
            self._due_at.pop(customer_id, None)

        return customer_id, self._on_flush(customer_id, "\n".join(messages))

    def flush_due(self) -> list[tuple[str, object]]:
        """Flush every batch whose fixed cooldown deadline has arrived."""
        now = self._clock()
        with self._lock:
            ready = [
                customer_id
                for customer_id, due_at in self._due_at.items()
                if due_at <= now
            ]
        return [
            result
            for customer_id in ready
            if (result := self.flush_customer(customer_id)) is not None
        ]

    def pending_count(self, customer_id: str) -> int:
        with self._lock:
            return len(self._pending.get(customer_id, []))

    def pending_messages(self, customer_id: str) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._pending.get(customer_id, ()))

    def pending_customers(self) -> list[str]:
        with self._lock:
            return list(self._pending)

    def due_in(self, customer_id: str) -> float:
        with self._lock:
            return self._due_in_locked(customer_id)

    def reset(self, customer_id: str) -> None:
        """Discard a customer's unprocessed messages for a new session."""
        with self._lock:
            self._pending.pop(customer_id, None)
            self._due_at.pop(customer_id, None)

    def _due_in_locked(self, customer_id: str) -> float:
        due_at = self._due_at.get(customer_id)
        if due_at is None:
            return 0.0
        return max(0.0, due_at - self._clock())


class MessageDebouncer:
    """按客户聚合连发消息：静默 window 秒后合并处理一次。

    Args:
        on_flush: 批次处理回调，签名 (customer_id, combined_text) -> 结果。
        clock: 可注入时钟（测试用 fake clock + 手动 flush_due）。
        window_seconds: 静默窗口，最后一条消息过去这么久即触发聚合处理。
    """

    def __init__(
        self,
        on_flush: Callable[[str, str], object],
        clock: Callable[[], float] = time.monotonic,
        window_seconds: float = 3.0,
    ) -> None:
        self._on_flush = on_flush
        self._clock = clock
        self._window = window_seconds
        self._pending: dict[str, list[str]] = {}
        self._last_at: dict[str, float] = {}
        self._lock = threading.Lock()

    def feed(self, customer_id: str, text: str) -> int:
        """缓存一条消息（刷新静默计时），返回该客户当前待处理条数。"""
        with self._lock:
            self._pending.setdefault(customer_id, []).append(text)
            self._last_at[customer_id] = self._clock()
            return len(self._pending[customer_id])

    def flush_due(self) -> list[tuple[str, object]]:
        """处理所有静默期满的客户，返回 (customer_id, 处理结果) 列表。"""
        now = self._clock()
        with self._lock:
            ready = [cid for cid, t in self._last_at.items() if now - t >= self._window]
        return [r for cid in ready if (r := self.flush_customer(cid)) is not None]

    def flush_customer(self, customer_id: str) -> tuple[str, object] | None:
        """立即处理指定客户的待处理批次（无论静默期是否满）。"""
        with self._lock:
            texts = self._pending.pop(customer_id, None)
            self._last_at.pop(customer_id, None)
        if not texts:
            return None
        # 连发消息按顺序拼接为一条逻辑消息，送入完整管道
        combined = "\n".join(texts)
        return customer_id, self._on_flush(customer_id, combined)

    def pending_count(self, customer_id: str) -> int:
        with self._lock:
            return len(self._pending.get(customer_id, []))

    def pending_customers(self) -> list[str]:
        with self._lock:
            return list(self._pending)
