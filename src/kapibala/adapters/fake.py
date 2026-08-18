"""Fake 适配器：返回脚本化 Estimation，使确定性逻辑与端到端管道不依赖网络。

仅用于测试与离线 demo。题目要求意图判断必须经过真实 LLM——
正式演示请配置 GEMINI_API_KEY 使用 GeminiAdapter（M3）。
"""

from __future__ import annotations

from collections.abc import Callable

from kapibala.adapters.base import LLMAdapter, LLMError
from kapibala.schemas import Estimation


class FakeAdapter(LLMAdapter):
    """脚本化适配器。

    - script() 预先排入 Estimation 或异常（LLMError 用于演练 fail-closed）；
    - 队列空时回落到 responder callable；
    - 两者都没有时抛 LLMError。
    """

    def __init__(self, responder: Callable[[str], Estimation] | None = None) -> None:
        self._queue: list[Estimation | Exception] = []
        self._responder = responder
        self.calls: list[str] = []  # 记录被调用的消息，便于断言"静默时未调用 LLM"

    def script(self, item: Estimation | Exception) -> None:
        self._queue.append(item)

    def estimate(self, message: str) -> Estimation:
        self.calls.append(message)
        if self._queue:
            item = self._queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        if self._responder is not None:
            return self._responder(message)
        raise LLMError("fake adapter: no scripted response")
