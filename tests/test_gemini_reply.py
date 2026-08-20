"""GeminiReplyGenerator 与 GeminiAdapter.generate_text 的单元测试（不碰网络）。"""

from kapibala.adapters.base import LLMError
from kapibala.adapters.gemini import GeminiAdapter
from kapibala.gemini_reply import GeminiReplyGenerator
from kapibala.output_guard import CANARY_TOKEN
from kapibala.reply_generator import TemplateReplyGenerator
from kapibala.schemas import ReplyKind


class TextResponse:
    def __init__(self, text):
        self.text = text


class FakeModels:
    def __init__(self, script):
        self._script = list(script)
        self.kwargs_seen: list[dict] = []

    def generate_content(self, **kwargs):
        self.kwargs_seen.append(kwargs)
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeClient:
    def __init__(self, script):
        self.models = FakeModels(script)


def test_generate_text_returns_stripped_text():
    client = FakeClient([TextResponse("  您好，我们的方案在 1-5 万美元区间。 ")])
    adapter = GeminiAdapter(client=client)
    assert adapter.generate_text("写一句报价回复") == "您好，我们的方案在 1-5 万美元区间。"


def test_generate_text_empty_raises_and_retries():
    client = FakeClient([TextResponse(""), TextResponse("好的")])
    adapter = GeminiAdapter(client=client, max_retries=1)
    assert adapter.generate_text("x") == "好的"
    assert len(client.models.kwargs_seen) == 2


def test_generate_text_exhausted_raises_llmerror():
    client = FakeClient([RuntimeError("boom"), RuntimeError("boom")])
    adapter = GeminiAdapter(client=client, max_retries=1)
    try:
        adapter.generate_text("x")
        assert False, "应当抛 LLMError"
    except LLMError:
        pass


def test_reply_generator_prompt_embeds_canary():
    client = FakeClient([TextResponse("可以呀，我给您介绍一下。")])
    adapter = GeminiAdapter(client=client)
    gen = GeminiReplyGenerator(adapter)
    reply = gen.generate(ReplyKind.PITCH, "你们产品怎么卖？")
    assert reply == "可以呀，我给您介绍一下。"
    kwargs = client.models.kwargs_seen[0]
    # canary 现在埋在 system_instruction 中；客户消息经 JSON 包装进入 contents
    assert CANARY_TOKEN in kwargs["config"].system_instruction
    assert CANARY_TOKEN not in kwargs["contents"]
    assert "你们产品怎么卖？" in kwargs["contents"]


def test_reply_generator_falls_back_on_llmerror():
    client = FakeClient([RuntimeError("boom")])
    adapter = GeminiAdapter(client=client, max_retries=0)
    gen = GeminiReplyGenerator(adapter)
    reply = gen.generate(ReplyKind.PITCH, "你们产品怎么卖？")
    assert reply == TemplateReplyGenerator().generate(ReplyKind.PITCH, "你们产品怎么卖？")
