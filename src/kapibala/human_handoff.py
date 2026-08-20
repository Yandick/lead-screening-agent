"""Narrow deterministic guard for explicit requests to speak with a human.

This detector recognizes a documented set of Chinese and English handoff
requests after Unicode, case, punctuation, and whitespace normalization. It is
deliberately not a general semantic classifier or prompt-injection defense:
ambiguous mentions, identity questions, and explicit negations continue to the
ordinary LLM classifier.
"""

from __future__ import annotations

import re
import unicodedata


_CHINESE_TARGET = (
    r"(?:真人(?:客服)?|人工(?:客服|服务)?|客服(?:人员|代表)|工作人员|"
    r"你们的?(?:客服|人员|员工|同事)|你们的人)"
)

_CHINESE_NEGATIONS = (
    re.compile(
        rf"(?:不需要|不想|不要|不用|无需|无须|别)(?:再)?"
        rf"(?:给我|帮我|让我)?(?:转|转接|切换|接入|找|联系|安排|叫|让|和|跟)?"
        rf"{_CHINESE_TARGET}"
    ),
    re.compile(rf"{_CHINESE_TARGET}(?:暂时)?(?:不需要|不用|不要了)"),
)

_CHINESE_REQUESTS = (
    re.compile(
        rf"(?:请|麻烦|帮我|给我|能否|可以|请问)?(?:给我|帮我|让我)?"
        rf"(?:转|转接|切换|接入)(?:到|成|为)?{_CHINESE_TARGET}"
    ),
    re.compile(rf"(?:我)?(?:想|要|希望|需要)(?:找|联系){_CHINESE_TARGET}"),
    re.compile(
        rf"(?:我)?(?:想|要|希望|需要)(?:和|跟){_CHINESE_TARGET}"
        rf"(?:说|聊|沟通|对话|交流)"
    ),
    re.compile(
        rf"(?:请|麻烦)?(?:让|叫|安排){_CHINESE_TARGET}(?:来)?"
        rf"(?:联系|回复|回电|给我打电话)"
    ),
    re.compile(
        rf"(?:请|麻烦){_CHINESE_TARGET}(?:来)?"
        rf"(?:联系|回复|回电|给我打电话)"
    ),
    re.compile(rf"(?:我)?(?:想要|要|需要){_CHINESE_TARGET}"),
)

_ENGLISH_TARGET = (
    r"(?:a )?(?:human(?: agent| representative)?|real person|live agent|"
    r"customer service (?:agent|representative|rep)|"
    r"support (?:agent|representative)|sales representative|staff member|"
    r"representative|supervisor|manager|"
    r"someone from (?:your|the) (?:team|company|staff))"
)

_ENGLISH_NEGATIONS = (
    re.compile(
        rf"(?:i )?(?:do not|don t|dont|would rather not|prefer not to|"
        rf"no need to|please do not|please don t|please dont)"
        rf"(?: [a-z]+){{0,6}} {_ENGLISH_TARGET}"
    ),
)

_ENGLISH_REQUESTS = (
    re.compile(
        rf"(?:please )?(?:connect|transfer|put|route|escalate) me "
        rf"(?:to|with) {_ENGLISH_TARGET}"
    ),
    re.compile(
        rf"(?:i (?:want|need|would like|d like|prefer) to|please let me|"
        rf"let me|can i|could i|may i) "
        rf"(?:talk|speak|chat|communicate) (?:to|with) {_ENGLISH_TARGET}"
    ),
    re.compile(
        rf"(?:can|could|would) you (?:let me )?"
        rf"(?:talk|speak|chat|communicate) (?:to|with) {_ENGLISH_TARGET}"
    ),
    re.compile(
        rf"(?:please )?(?:have|ask|get) {_ENGLISH_TARGET} (?:to )?"
        rf"(?:contact|call|phone|email|message|reach) me"
    ),
    re.compile(rf"(?:i (?:want|need|would like)|get me) {_ENGLISH_TARGET}"),
)


def _normalize(message: str) -> str:
    """Normalize compatibility forms and replace punctuation with spaces."""
    normalized = unicodedata.normalize("NFKC", message).casefold()
    characters = (
        " " if unicodedata.category(character)[0] in {"P", "S", "Z"} else character
        for character in normalized
    )
    return " ".join("".join(characters).split())


def is_explicit_human_request(message: str) -> bool:
    """Return true only for a supported, non-negated human handoff request."""
    normalized = _normalize(message)
    compact = normalized.replace(" ", "")

    if any(pattern.search(compact) for pattern in _CHINESE_NEGATIONS):
        return False
    if any(pattern.search(normalized) for pattern in _ENGLISH_NEGATIONS):
        return False

    return any(pattern.search(compact) for pattern in _CHINESE_REQUESTS) or any(
        pattern.search(normalized) for pattern in _ENGLISH_REQUESTS
    )
