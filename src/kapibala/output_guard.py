"""输出防护：约束 4 的确定性检查层（在回复进入统一发送函数之前）。

机制（plan2 第 4 节）：
1. canary 哨兵——系统提示词中埋入 CANARY_TOKEN，回复中命中 = 提示词泄露；
2. 密钥/凭证正则——常见 API key 形态与 "key=xxx" 赋值形态；
3. 长度与格式合规检查。

任一命中：丢弃原文，替换为泛化安全回复。这是确定性检查，
不依赖"用 prompt 防 prompt 泄露"。

已知局限（README 需保留声明）：无法覆盖所有隐晦改写、编码和多轮诱导，
自然语言泄露防御做不到 100%。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: 埋入生成侧系统提示词的哨兵字符串（M3 接入 LLM 生成时使用）。
#: 回复中出现它即判定为系统提示词泄露。
CANARY_TOKEN = "ZQX9182-INTERNAL"

#: 命中任一检查后的泛化安全回复；不得声称执行了实际不存在的业务动作。
SAFE_FALLBACK = "抱歉，我暂时无法回答这个问题。您可以换个方式描述需求。"

#: 回复最大长度（字符）
MAX_REPLY_LENGTH = 500

_SECRET_PATTERNS = [
    # Google API key
    re.compile(r"AIza[0-9A-Za-z_\-]{35}"),
    # OpenAI 形态 sk-xxx
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),
    # 通用凭证赋值形态：api_key=... / secret: ... / token = ... 等
    re.compile(r"(?i)(api[_-]?key|secret|token|password|凭证|密钥)\s*[:=：]\s*\S+"),
]


@dataclass(frozen=True)
class GuardOutcome:
    """输出检查结果。passed=False 时 text 已被替换为安全回复。"""

    text: str
    passed: bool
    reason: str = ""


def sanitize(text: str) -> GuardOutcome:
    """对一条待发送回复做确定性检查，不合格则替换为泛化安全回复。"""
    if not text or not text.strip():
        return GuardOutcome(SAFE_FALLBACK, False, "empty")
    if CANARY_TOKEN in text:
        return GuardOutcome(SAFE_FALLBACK, False, "canary_leak")
    for pattern in _SECRET_PATTERNS:
        if pattern.search(text):
            return GuardOutcome(SAFE_FALLBACK, False, "credential_pattern")
    if len(text) > MAX_REPLY_LENGTH:
        return GuardOutcome(SAFE_FALLBACK, False, "too_long")
    return GuardOutcome(text, True)
