"""Agent runtime input validation and bounded per-customer conversation history."""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from enum import Enum


DEFAULT_MAX_MESSAGE_LENGTH = 4000
DEFAULT_MAX_HISTORY_TURNS = 20


class InputValidationError(ValueError):
    """Raised when untrusted runtime input is not safe to process."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class RuntimeConfig:
    """Limits applied before the Agent loads state or invokes an LLM."""

    max_message_length: int = DEFAULT_MAX_MESSAGE_LENGTH
    max_history_turns: int = DEFAULT_MAX_HISTORY_TURNS

    def __post_init__(self) -> None:
        if self.max_message_length <= 0:
            raise ValueError("max_message_length must be positive")
        if self.max_history_turns <= 0:
            raise ValueError("max_history_turns must be positive")


@dataclass(frozen=True)
class RuntimeInput:
    """Validated, normalized input for one customer-message processing run."""

    customer_id: str
    message: str

    @classmethod
    def validate(
        cls, customer_id: object, message: object, config: RuntimeConfig
    ) -> RuntimeInput:
        if not isinstance(customer_id, str) or not customer_id.strip():
            raise InputValidationError("invalid_customer_id")
        if not isinstance(message, str) or not message.strip():
            raise InputValidationError("blank_message")
        if len(message) > config.max_message_length:
            raise InputValidationError("message_too_long")
        return cls(customer_id=customer_id.strip(), message=message)


class ConversationRole(str, Enum):
    CUSTOMER = "customer"
    ASSISTANT = "assistant"


@dataclass(frozen=True)
class ConversationTurn:
    """One accepted inbound message or one confirmed outbound reply."""

    role: ConversationRole
    content: str


class ConversationStore:
    """Thread-safe bounded histories isolated by normalized customer ID.

    ``max_turns`` counts individual utterances, so a successful customer/reply
    exchange normally consumes two turns. Oldest turns are discarded first.
    """

    def __init__(self, max_turns: int = DEFAULT_MAX_HISTORY_TURNS) -> None:
        if max_turns <= 0:
            raise ValueError("max_turns must be positive")
        self._max_turns = max_turns
        self._histories: dict[str, deque[ConversationTurn]] = {}
        self._lock = threading.Lock()

    @property
    def max_turns(self) -> int:
        return self._max_turns

    def append_customer(self, customer_id: str, content: str) -> None:
        self._append(customer_id, ConversationRole.CUSTOMER, content)

    def append_assistant(self, customer_id: str, content: str) -> None:
        self._append(customer_id, ConversationRole.ASSISTANT, content)

    def get(self, customer_id: str) -> tuple[ConversationTurn, ...]:
        """Return an immutable oldest-to-newest snapshot without creating history."""
        with self._lock:
            return tuple(self._histories.get(customer_id, ()))

    def _append(self, customer_id: str, role: ConversationRole, content: str) -> None:
        with self._lock:
            history = self._histories.setdefault(
                customer_id, deque(maxlen=self._max_turns)
            )
            history.append(ConversationTurn(role=role, content=content))
