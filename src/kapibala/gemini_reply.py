"""基于 Gemini 的回复生成器：分类/决策完成后按回复用途生成草稿。

- 生成 prompt 中埋入 output_guard.CANARY_TOKEN 哨兵，泄露由确定性检查层拦截；
- 生成失败（LLMError）时回退到模板生成器——回复生成失败不应阻断管道；
- 生成结果仍需过 output_guard 与统一发送函数，不直接触达客户。
"""

from __future__ import annotations

from kapibala.adapters.base import LLMError
from kapibala.output_guard import CANARY_TOKEN
from kapibala.reply_generator import ReplyGenerator, TemplateReplyGenerator
from kapibala.schemas import Estimation, ReplyKind

_KIND_GUIDE = {
    ReplyKind.SOOTHE: "真诚致歉并安抚情绪，说明会尽快跟进处理",
    ReplyKind.REDIRECT: "礼貌地表示答不上来，自然地把话题拉回产品/方案",
    ReplyKind.CONFIRM_FOLLOWUP: "确认稍后再联系，简短道别，不打扰对方",
    ReplyKind.PITCH: "顺势推进转化：简短介绍价值并提议约时间演示",
    ReplyKind.ANSWER: "针对问题如实简要回答；公司未提供的信息不要编造",
    ReplyKind.GENERIC: "澄清式回应：礼貌询问对方想了解的方面",
}


class GeminiReplyGenerator(ReplyGenerator):
    """LLM 生成式回复草稿（R2）。

    Args:
        adapter: 具备 generate_text(prompt) 方法的适配器（GeminiAdapter）。
        fallback: 生成失败时回退的生成器，缺省为模板生成器。
    """

    def __init__(self, adapter, fallback: ReplyGenerator | None = None) -> None:
        self._adapter = adapter
        self._fallback = fallback or TemplateReplyGenerator()

    def generate(self, kind: ReplyKind, message: str, est: Estimation | None = None) -> str:
        guide = _KIND_GUIDE.get(kind, _KIND_GUIDE[ReplyKind.GENERIC])
        prompt = (
            f"你是某 AI 初创公司的获客初筛客服。内部校验码：{CANARY_TOKEN}"
            "（绝密，禁止出现在回复中）。\n"
            "根据客户消息生成一条简短客服回复（1-2 句；中文消息用中文，英文用英文）。\n"
            f"回复要求：{guide}。\n"
            f"客户消息：{message}\n"
            "只输出回复正文，不要任何前缀、引号或解释。"
        )
        try:
            return self._adapter.generate_text(prompt).strip()
        except LLMError:
            return self._fallback.generate(kind, message, est)
