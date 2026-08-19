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
