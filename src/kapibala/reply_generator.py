"""回复生成器接口与模板实现。

回复生成与意图分类是两次独立调用（plan2 第 4 节）；生成 prompt 不含任何
内部机密。M2 先用模板生成器打通管道，M3 增加 LLM 生成实现（其系统提示词
中埋入 output_guard.CANARY_TOKEN 哨兵）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from kapibala.schemas import Estimation, ReplyKind

_TEMPLATES = {
    ReplyKind.CLARIFY: "想确认一下，方便把您的需求再说得具体一点吗？我好为您安排合适的人对接。",
    ReplyKind.SOOTHE: "非常抱歉给您带来了不好的体验，您反馈的问题我已经记下了，我们会尽快跟进改进。",
    ReplyKind.REDIRECT: "哈哈这个我可答不上来。还是说回正事——您对我们的产品还有什么想了解的吗？",
    ReplyKind.CONFIRM_FOLLOWUP: "好的，那不打扰您了，我们稍后再联系，祝您顺利！",
    ReplyKind.PITCH: "太好了！我可以给您详细介绍一下方案，方便的话我们约个时间做个简短演示。",
    ReplyKind.ANSWER: "关于您的问题，我先把相关资料整理好发您；如果还有疑问随时问我。",
    ReplyKind.GENERIC: "收到。方便说说您主要想了解哪方面吗？我给您针对性介绍。",
}

#: 跟进触达话术（run_followups 使用）
FOLLOWUP_TEMPLATE = "您好，之前和您聊过我们的方案，想跟进一下您这边考虑得怎么样了？"


class ReplyGenerator(ABC):
    """回复生成器接口。"""

    @abstractmethod
    def generate(self, kind: ReplyKind, message: str, est: Estimation | None = None) -> str:
        """根据回复用途与客户消息生成回复草稿。

        生成结果不代表可以直接发送——必须经过 output_guard 检查与统一发送函数。
        """


class TemplateReplyGenerator(ReplyGenerator):
    """确定性模板生成器：离线 demo 与测试用，不依赖网络。"""

    def generate(self, kind: ReplyKind, message: str, est: Estimation | None = None) -> str:
        return _TEMPLATES[kind]
