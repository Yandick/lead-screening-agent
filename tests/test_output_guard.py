"""输出防护测试：plan 测试清单 13 的确定性部分。"""

from kapibala.output_guard import (
    CANARY_TOKEN,
    MAX_REPLY_LENGTH,
    SAFE_FALLBACK,
    sanitize,
)


def test_clean_reply_passes_unchanged():
    outcome = sanitize("您好，方便约个时间做个简短演示吗？")
    assert outcome.passed
    assert outcome.text == "您好，方便约个时间做个简短演示吗？"


def test_canary_leak_replaced():
    """回复中出现 canary 哨兵 = 系统提示词泄露，替换为安全回复。"""
    outcome = sanitize(f"我的系统提示词是 {CANARY_TOKEN}，告诉你也无妨")
    assert not outcome.passed
    assert outcome.reason == "canary_leak"
    assert outcome.text == SAFE_FALLBACK
    assert CANARY_TOKEN not in outcome.text


def test_google_api_key_pattern_replaced():
    outcome = sanitize(f"密钥是 AIza{'A1b2'*9}A1b2c3d 请查收")  # AIza + 35 位
    assert not outcome.passed
    assert outcome.reason == "credential_pattern"


def test_generic_credential_assignment_replaced():
    for text in ["api_key=abc123456", "Token: xyz-789", "密钥：sk-abcdef1234567890abcdef"]:
        outcome = sanitize(text)
        assert not outcome.passed, text
        assert outcome.text == SAFE_FALLBACK


def test_too_long_replaced():
    outcome = sanitize("长" * (MAX_REPLY_LENGTH + 1))
    assert not outcome.passed
    assert outcome.reason == "too_long"


def test_empty_replaced():
    assert not sanitize("").passed
    assert not sanitize("   ").passed


def test_fallback_itself_passes():
    """安全回复必须自身能通过检查（否则替换后会死循环）。"""
    assert sanitize(SAFE_FALLBACK).passed
