"""回复生成器接口与模板实现。

回复生成与结构化分类职责分离（plan2 第 4 节）；生成 prompt 不含任何
内部机密。M2 先用模板生成器打通管道，R2 增加 LLM 生成实现
（gemini_reply.GeminiReplyGenerator，其提示词中埋入 output_guard.CANARY_TOKEN 哨兵）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from kapibala.context import ReplyRequest
from kapibala.schemas import Estimation, ReplyKind

_TEMPLATES = {
    ReplyKind.SOOTHE: "非常抱歉给您带来了不好的体验，您反馈的问题我已经记下了，我们会尽快跟进改进。",
    ReplyKind.REDIRECT: "哈哈这个我可答不上来。还是说回正事——您对我们的产品还有什么想了解的吗？",
    ReplyKind.PITCH: "谢谢您的关注。您最想了解哪方面？我会先核实相关信息再回复。",
    ReplyKind.ANSWER: "这个问题需要核对已确认的产品信息，我先帮您确认，避免给出不准确的答复。",
    ReplyKind.GENERIC: "收到。方便说说您主要想了解哪方面吗？我给您针对性介绍。",
}

#: 跟进触达话术（run_followups 使用）
FOLLOWUP_TEMPLATE = "您好，之前和您聊过我们的方案，想跟进一下您这边考虑得怎么样了？"

#: 转人工状态切换时发送的受控通知。该文本不经过 LLM 生成。
HANDOFF_NOTICE = "已为您转接人工同事，后续将由人工跟进。"


class ReplyGenerator(ABC):
    """回复生成器接口。"""

    @abstractmethod
    def generate(self, kind: ReplyKind, message: str, est: Estimation | None = None) -> str:
        """根据回复用途与客户消息生成回复草稿。

        生成结果不代表可以直接发送——必须经过 output_guard 检查与统一发送函数。
        """

    def generate_request(self, request: ReplyRequest) -> str:
        """Generate from a structured request.

        The default keeps deterministic and test generators compatible while
        context-aware generators can consume every request field.
        """
        estimation = Estimation(
            intent=request.intent,
            dissatisfied=request.dissatisfied,
        )
        return self.generate(request.reply_kind, request.message, estimation)


class TemplateReplyGenerator(ReplyGenerator):
    """确定性模板生成器：离线 demo 与测试用，不依赖网络。"""

    def generate(self, kind: ReplyKind, message: str, est: Estimation | None = None) -> str:
        return _TEMPLATES[kind]
