"""M0 冒烟测试：包可导入、schema 与适配器接口符合约定。"""

import pytest

from kapibala.adapters import LLMAdapter
from kapibala.adapters.base import LLMError
from kapibala.schemas import Estimation, Intent


def test_intent_enum_has_five_values():
    assert {i.value for i in Intent} == {
        "interested",
        "needs_info",
        "rejected",
        "off_topic",
        "other",
    }


def test_estimation_defaults():
    est = Estimation(intent=Intent.INTERESTED)
    assert est.dissatisfied is False
    assert est.followup_requested is False
    assert est.confidence == 1.0
    assert est.reason == ""


def test_llm_adapter_is_abstract():
    with pytest.raises(TypeError):
        LLMAdapter()  # type: ignore[abstract]


def test_llm_error_is_exception():
    assert issubclass(LLMError, Exception)
