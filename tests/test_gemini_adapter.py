"""GeminiAdapter 双分类调用测试：注入假 client，不碰网络。"""

import json

import pytest

from kapibala.adapters.base import LLMError
from kapibala.adapters.gemini import (
    GeminiAdapter,
    _DissatisfactionOut,
    _DISSATISFACTION_RESPONSE_SCHEMA,
    _IntentOut,
    _INTENT_RESPONSE_SCHEMA,
)
from kapibala.schemas import Estimation, Intent


class FakeResponse:
    def __init__(self, parsed, *, include_text=False):
        self.parsed = parsed
        if include_text:
            if isinstance(parsed, dict):
                payload = parsed
            elif parsed is None:
                payload = None
            else:
                payload = parsed.model_dump(mode="json")
            self.text = json.dumps(payload)


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


def good_intent(intent=Intent.INTERESTED):
    return _IntentOut(intent=intent)


def good_dissatisfaction(dissatisfied=False):
    return _DissatisfactionOut(dissatisfied=dissatisfied)


def test_success_combines_two_validated_results():
    client = FakeClient(
        [FakeResponse(good_intent()), FakeResponse(good_dissatisfaction(True))]
    )
    adapter = GeminiAdapter(client=client, model="test-model")

    est = adapter.estimate("你们产品怎么收费？")

    assert est == Estimation(intent=Intent.INTERESTED, dissatisfied=True)
    assert len(client.models.kwargs_seen) == 2


def test_calls_are_ordered_and_use_exact_independent_schemas():
    message = "hello"
    client = FakeClient(
        [FakeResponse(good_intent()), FakeResponse(good_dissatisfaction())]
    )
    adapter = GeminiAdapter(client=client, max_retries=0)

    adapter.estimate(message)

    intent_call, dissatisfaction_call = client.models.kwargs_seen
    assert intent_call["contents"] == message
    assert dissatisfaction_call["contents"] == message
    assert intent_call["config"].response_schema == _INTENT_RESPONSE_SCHEMA
    assert (
        dissatisfaction_call["config"].response_schema
        == _DISSATISFACTION_RESPONSE_SCHEMA
    )
    assert set(_IntentOut.model_json_schema()["properties"]) == {"intent"}
    assert set(_DissatisfactionOut.model_json_schema()["properties"]) == {
        "dissatisfied"
    }
    assert intent_call["config"].response_mime_type == "application/json"
    assert dissatisfaction_call["config"].response_mime_type == "application/json"


def test_dissatisfaction_request_does_not_receive_intent_result():
    message = "请分析这条原始消息"
    client = FakeClient(
        [FakeResponse(good_intent(Intent.REJECTED)), FakeResponse(good_dissatisfaction())]
    )
    adapter = GeminiAdapter(client=client, max_retries=0, few_shot=True)

    adapter.estimate(message)

    second_call = client.models.kwargs_seen[1]
    assert second_call["contents"] == message
    assert second_call["config"].response_schema == _DISSATISFACTION_RESPONSE_SCHEMA
    assert "rejected" not in second_call["config"].system_instruction
    assert "intent=" not in second_call["config"].system_instruction


def test_intent_failure_does_not_call_dissatisfaction():
    client = FakeClient([TimeoutError("intent timeout")])
    adapter = GeminiAdapter(client=client, max_retries=0)

    with pytest.raises(LLMError, match="intent"):
        adapter.estimate("hello")

    assert len(client.models.kwargs_seen) == 1
    assert (
        client.models.kwargs_seen[0]["config"].response_schema
        == _INTENT_RESPONSE_SCHEMA
    )


def test_dissatisfaction_failure_discards_intent_result():
    client = FakeClient(
        [FakeResponse(good_intent(Intent.INTERESTED)), TimeoutError("diss timeout")]
    )
    adapter = GeminiAdapter(client=client, max_retries=0)

    with pytest.raises(LLMError, match="dissatisfaction"):
        adapter.estimate("hello")

    assert len(client.models.kwargs_seen) == 2
    assert (
        client.models.kwargs_seen[1]["config"].response_schema
        == _DISSATISFACTION_RESPONSE_SCHEMA
    )


def test_raw_response_text_is_strictly_validated():
    client = FakeClient(
        [
            FakeResponse(
                {"intent": "interested", "unexpected": "control"},
                include_text=True,
            )
        ]
    )
    adapter = GeminiAdapter(client=client, max_retries=0)

    with pytest.raises(LLMError, match="intent"):
        adapter.estimate("hello")


@pytest.mark.parametrize(
    "bad_response",
    [
        {"intent": "interested", "dissatisfied": False},
        {"dissatisfied": "false"},
        None,
    ],
)
def test_invalid_or_extra_output_fails_closed(bad_response):
    first = FakeResponse(bad_response)
    if isinstance(bad_response, dict) and set(bad_response) == {"dissatisfied"}:
        script = [FakeResponse(good_intent()), first]
    else:
        script = [first]
    client = FakeClient(script)
    adapter = GeminiAdapter(client=client, max_retries=0)

    with pytest.raises(LLMError):
        adapter.estimate("hello")


def test_transient_intent_error_recovers_before_second_call():
    client = FakeClient(
        [
            TimeoutError("t"),
            FakeResponse(good_intent()),
            FakeResponse(good_dissatisfaction()),
        ]
    )
    adapter = GeminiAdapter(client=client, max_retries=2)

    est = adapter.estimate("hello")

    assert est.intent is Intent.INTERESTED
    assert len(client.models.kwargs_seen) == 3


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(LLMError, match="GEMINI_API_KEY"):
        GeminiAdapter()


def test_env_defaults(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "env-model")
    adapter = GeminiAdapter(client=FakeClient([]), model=None)
    assert adapter.model == "env-model"
