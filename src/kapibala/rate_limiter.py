"""滑动窗口限流器。

约束 1 的强制层：同一客户任意 60 秒窗口内最多 1 条客户可见消息。
只有统一发送函数调用 record() 写时间戳；LLM 重试、策略重算只调 allow()，
不占配额。
"""

from __future__ import annotations

import time
from collections.abc import Callable


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
        self._sent: dict[str, list[float]] = {}

    def _prune(self, customer_id: str, now: float) -> list[float]:
        """移除已滑出窗口的时间戳。"""
        kept = [ts for ts in self._sent.get(customer_id, []) if now - ts < self._window]
        self._sent[customer_id] = kept
        return kept

    def allow(self, customer_id: str) -> bool:
        """检查当前是否允许发送（只读检查，不记录、不占配额）。"""
        return len(self._prune(customer_id, self._clock())) < self._max

    def record(self, customer_id: str) -> None:
        """记录一次真实发送。只能由统一发送函数调用。"""
        now = self._clock()
        self._prune(customer_id, now).append(now)
