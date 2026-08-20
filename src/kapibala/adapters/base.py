"""LLM 适配器接口。

LLM 只负责输出结构化理解结果（Estimation），不直接决策、不执行动作。
具体适配器可以用多次独立调用构造该结果，但业务层只依赖 estimate 边界。
任何实现（Gemini / Fake / 其他）都必须满足：

- 输出严格映射为 Estimation，字段非法视为解析失败；
- 解析失败、超时、API 报错时抛出 LLMError，由上层 fail-closed 处理。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from kapibala.schemas import Estimation


class LLMError(Exception):
    """LLM 调用或解析失败。上层收到此异常必须 fail-closed。"""


class LLMAdapter(ABC):
    """结构化状态估计适配器接口。"""

    @abstractmethod
    def estimate(self, message: str) -> Estimation:
        """对一条客户消息做结构化状态估计。

        Args:
            message: 客户消息原文（不可信输入）。

        Returns:
            Estimation: 结构化估计结果。

        Raises:
            LLMError: 解析失败、字段非法、超时或 API 报错。
        """
