"""LLM 适配器：业务代码只依赖 LLMAdapter 接口，不依赖具体 SDK。"""

from kapibala.adapters.base import LLMAdapter

__all__ = ["LLMAdapter"]
