"""GeminiAdapter 单元测试：注入假 client，不碰网络（清单 12）。"""

import pytest

from kapibala.adapters.base import LLMError
from kapibala.adapters.gemini import GeminiAdapter, _EstimationOut
from kapibala.schemas import Estimation, Intent


class FakeResponse:
    def __init__(self, parsed):
        self.parsed = parsed


class FakeModels:
    """按脚本依次返回响应或抛异常。"""

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


def good_parsed(**overrides):
    data = {
        "intent": Intent.INTERESTED,
        "dissatisfied": False,
        "followup_requested": False,
        "confidence": 0.9,
        "reason": "ok",
    }
    data.update(overrides)
    return _EstimationOut(**data)


def test_success_returns_estimation():
    client = FakeClient([FakeResponse(good_parsed())])
    adapter = GeminiAdapter(client=client, model="test-model")
    est = adapter.estimate("你们产品怎么收费？")
    assert isinstance(est, Estimation)
    assert est.intent is Intent.INTERESTED
    assert est.confidence == 0.9


def test_request_uses_structured_output():
    client = FakeClient([FakeResponse(good_parsed())])
    adapter = GeminiAdapter(client=client)
    adapter.estimate("hello")
    kwargs = client.models.kwargs_seen[0]
    config = kwargs["config"]
    assert kwargs["contents"] == "hello"
    assert config.response_mime_type == "application/json"
    assert config.response_schema is _EstimationOut


def test_schema_violation_fails_closed_after_retries():
    client = FakeClient([FakeResponse(None), FakeResponse(None), FakeResponse(None)])
    adapter = GeminiAdapter(client=client, max_retries=2)
    with pytest.raises(LLMError):
        adapter.estimate("hello")
    assert len(client.models.kwargs_seen) == 3  # 1 + 2 次重试


def test_api_error_fails_closed_after_retries():
    client = FakeClient([TimeoutError("t"), ConnectionError("c"), RuntimeError("x")])
    adapter = GeminiAdapter(client=client, max_retries=2)
    with pytest.raises(LLMError):
        adapter.estimate("hello")
    assert len(client.models.kwargs_seen) == 3


def test_transient_error_recovers_on_retry():
    client = FakeClient([TimeoutError("t"), FakeResponse(good_parsed())])
    adapter = GeminiAdapter(client=client, max_retries=2)
    est = adapter.estimate("hello")
    assert est.intent is Intent.INTERESTED


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(LLMError, match="GEMINI_API_KEY"):
        GeminiAdapter()


def test_env_defaults(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "env-model")
    adapter = GeminiAdapter(client=FakeClient([]), model=None)
    assert adapter.model == "env-model"
