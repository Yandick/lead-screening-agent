"""Local Web UI and JSON API for the lead-screening demo."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from threading import RLock
from urllib.parse import parse_qs, urlparse

from kapibala.agent import ProcessResult, ScreeningAgent
from kapibala.adapters.base import LLMError
from kapibala.adapters.fake import FakeAdapter
from kapibala.audit import AuditLog
from kapibala.executor import Executor
from kapibala.followup import FollowupValidationError, FollowupQueue
from kapibala.rate_limiter import SlidingWindowRateLimiter
from kapibala.reply_generator import TemplateReplyGenerator
from kapibala.schemas import Estimation, Intent
from kapibala.state_machine import StateMachine

MAX_REQUEST_BYTES = 64 * 1024


class WebRequestError(ValueError):
    """A client-visible API error with a stable HTTP status."""

    def __init__(self, message: str, status: HTTPStatus = HTTPStatus.BAD_REQUEST):
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass
class WebApplication:
    """Thread-safe facade exposing only demo-safe Agent operations."""

    agent: ScreeningAgent
    state_machine: StateMachine
    adapter: object
    audit: AuditLog
    mode: str
    clock: object = time.monotonic

    def __post_init__(self) -> None:
        self._lock = RLock()

    @property
    def is_fake(self) -> bool:
        return isinstance(self.adapter, FakeAdapter)

    def meta(self) -> dict[str, object]:
        return {"mode": self.mode, "fake": self.is_fake}

    def process_message(self, payload: dict[str, object]) -> dict[str, object]:
        with self._lock:
            result = self.agent.handle_message(
                payload.get("customer_id"), payload.get("message")
            )
            customer_id = self._normalized_id(payload.get("customer_id"), required=False)
            return {
                "result": self._serialize_result(result),
                "customer": (
                    self.customer(customer_id)
                    if customer_id and result.note != "invalid_input"
                    else None
                ),
            }

    def schedule_followup(self, payload: dict[str, object]) -> dict[str, object]:
        with self._lock:
            try:
                followup, execution = self.agent.schedule_followup(
                    payload.get("customer_id"),
                    payload.get("delay_seconds"),
                    payload.get("context", ""),
                )
            except FollowupValidationError as exc:
                raise WebRequestError(exc.reason) from exc
            return {
                "followup": self._serialize_followup(followup),
                "execution": {
                    "executed": execution.executed,
                    "reason": execution.reason,
                },
                "customer": self.customer(followup.customer_id),
            }

    def run_followups(self) -> dict[str, object]:
        with self._lock:
            outcomes = self.agent.run_followups()
            return {
                "outcomes": [
                    {
                        "followup": self._serialize_followup(followup),
                        "execution": {
                            "executed": execution.executed,
                            "reason": execution.reason,
                        },
                    }
                    for followup, execution in outcomes
                ],
                "pending": self.followups(),
            }

    def reactivate(self, payload: dict[str, object]) -> dict[str, object]:
        customer_id = self._normalized_id(payload.get("customer_id"))
        with self._lock:
            self.state_machine.reactivate(customer_id)
            self.audit.record(customer_id, "reactivated", "by human operator")
            return self.customer(customer_id)

    def script(self, payload: dict[str, object]) -> dict[str, object]:
        if not self.is_fake:
            raise WebRequestError(
                "script is only available in fake mode", HTTPStatus.CONFLICT
            )
        with self._lock:
            if payload.get("error") is True:
                self.adapter.script(LLMError("scripted error"))
                return {"queued": "error"}
            try:
                dissatisfied = payload.get("dissatisfied", False)
                if not isinstance(dissatisfied, bool):
                    raise ValueError("dissatisfied must be boolean")
                estimation = Estimation(
                    intent=Intent(payload.get("intent", Intent.OTHER.value)),
                    dissatisfied=dissatisfied,
                )
            except (TypeError, ValueError) as exc:
                raise WebRequestError(str(exc)) from exc
            self.adapter.script(estimation)
            return {
                "queued": {
                    "intent": estimation.intent.value,
                    "dissatisfied": estimation.dissatisfied,
                }
            }

    def customer(self, customer_id: object) -> dict[str, object]:
        normalized_id = self._normalized_id(customer_id)
        with self._lock:
            state = self.state_machine.get(normalized_id)
            history = self.agent.conversation_store.get(normalized_id)
            return {
                "customer_id": normalized_id,
                "state": {
                    "session": state.session.value,
                    "anomaly_count": state.anomaly_count,
                    "last_estimation": (
                        {
                            "intent": state.last_estimation.intent.value,
                            "dissatisfied": state.last_estimation.dissatisfied,
                        }
                        if state.last_estimation is not None
                        else None
                    ),
                },
                "history": [
                    {"role": turn.role.value, "content": turn.content}
                    for turn in history
                ],
                "history_limit": self.agent.conversation_store.max_turns,
                "followups": [
                    self._serialize_followup(item)
                    for item in self.agent.pending_followups(normalized_id)
                ],
                "audit": [
                    {
                        "at": event.at,
                        "event": event.event,
                        "detail": event.detail,
                    }
                    for event in self.audit.events_for(normalized_id)
                ],
            }

    def followups(self, customer_id: object | None = None) -> list[dict[str, object]]:
        normalized_id = (
            self._normalized_id(customer_id) if customer_id is not None else None
        )
        with self._lock:
            return [
                self._serialize_followup(item)
                for item in self.agent.pending_followups(normalized_id)
            ]

    def _serialize_followup(self, followup) -> dict[str, object]:
        return {
            "customer_id": followup.customer_id,
            "due_at": followup.due_at,
            "due_in_seconds": max(0.0, followup.due_at - self.clock()),
            "context": followup.context,
        }

    @staticmethod
    def _serialize_result(result: ProcessResult) -> dict[str, object]:
        return {
            "note": result.note,
            "input_error": result.input_error,
            "estimation": (
                {
                    "intent": result.estimation.intent.value,
                    "dissatisfied": result.estimation.dissatisfied,
                }
                if result.estimation is not None
                else None
            ),
            "transition": {
                "escalated_now": result.transition.escalated_now,
                "closed_now": result.transition.closed_now,
            },
            "actions": (
                [action.value for action in result.decision.actions]
                if result.decision is not None
                else []
            ),
            "executions": [
                {"executed": item.executed, "reason": item.reason}
                for item in result.executions
            ],
            "reply_text": result.reply_text,
        }

    @staticmethod
    def _normalized_id(customer_id: object, *, required: bool = True) -> str:
        if isinstance(customer_id, str) and customer_id.strip():
            return customer_id.strip()
        if not required:
            return ""
        raise WebRequestError("invalid_customer_id")


def build_web_application(sink=None) -> WebApplication:
    """Build the same Agent pipeline used by the demo, without a real IM sink."""
    clock = time.monotonic
    state_machine = StateMachine()
    limiter = SlidingWindowRateLimiter(clock=clock)
    audit = AuditLog(clock=clock)
    followups = FollowupQueue()
    sink = sink or (lambda _customer_id, _text: None)
    executor = Executor(state_machine, limiter, sink, audit, followups)

    if os.environ.get("GEMINI_API_KEY"):
        from kapibala.adapters.gemini import GeminiAdapter
        from kapibala.gemini_reply import GeminiReplyGenerator

        adapter: object = GeminiAdapter()
        generator = GeminiReplyGenerator(adapter)
        mode = f"gemini · {adapter.model}"
    else:
        adapter = FakeAdapter(
            responder=lambda _message: Estimation(Intent.OTHER, False)
        )
        generator = TemplateReplyGenerator()
        mode = "fake · deterministic"

    agent = ScreeningAgent(
        adapter,
        generator,
        executor,
        state_machine,
        audit,
        followups,
        clock=clock,
    )
    return WebApplication(agent, state_machine, adapter, audit, mode, clock)


def _asset(name: str) -> bytes:
    return files("kapibala").joinpath("static", name).read_bytes()


def make_handler(application: WebApplication):
    class Handler(BaseHTTPRequestHandler):
        server_version = "KapibalaDemo/1.0"

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/":
                return self._send_bytes(_asset("index.html"), "text/html; charset=utf-8")
            if parsed.path == "/assets/web.css":
                return self._send_bytes(_asset("web.css"), "text/css; charset=utf-8")
            if parsed.path == "/assets/web.js":
                return self._send_bytes(
                    _asset("web.js"), "text/javascript; charset=utf-8"
                )
            try:
                if parsed.path == "/api/meta":
                    return self._send_json(application.meta())
                if parsed.path == "/api/customer":
                    query = parse_qs(parsed.query)
                    return self._send_json(
                        application.customer(query.get("customer_id", [None])[0])
                    )
                if parsed.path == "/api/followups":
                    query = parse_qs(parsed.query)
                    return self._send_json(
                        {"followups": application.followups(query.get("customer_id", [None])[0])}
                    )
            except WebRequestError as exc:
                return self._send_json({"error": exc.message}, exc.status)
            self._send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            try:
                payload = self._read_json()
                routes = {
                    "/api/messages": application.process_message,
                    "/api/followups": application.schedule_followup,
                    "/api/followups/run": lambda _payload: application.run_followups(),
                    "/api/reactivate": application.reactivate,
                    "/api/script": application.script,
                }
                handler = routes.get(parsed.path)
                if handler is None:
                    return self._send_json(
                        {"error": "not_found"}, HTTPStatus.NOT_FOUND
                    )
                self._send_json(handler(payload))
            except WebRequestError as exc:
                self._send_json({"error": exc.message}, exc.status)

        def _read_json(self) -> dict[str, object]:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise WebRequestError("invalid_content_length") from exc
            if length <= 0 or length > MAX_REQUEST_BYTES:
                raise WebRequestError("invalid_request_size")
            try:
                payload = json.loads(self.rfile.read(length))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise WebRequestError("invalid_json") from exc
            if not isinstance(payload, dict):
                raise WebRequestError("json_object_required")
            return payload

        def _send_json(
            self, payload: object, status: HTTPStatus = HTTPStatus.OK
        ) -> None:
            self._send_bytes(
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
                status,
            )

        def _send_bytes(
            self,
            payload: bytes,
            content_type: str,
            status: HTTPStatus = HTTPStatus.OK,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    return Handler


def create_server(
    application: WebApplication, host: str = "127.0.0.1", port: int = 8765
) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), make_handler(application))


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv()
    application = build_web_application()
    host = os.environ.get("KAPIBALA_HOST", "127.0.0.1")
    port = int(os.environ.get("KAPIBALA_PORT", "8765"))
    server = create_server(application, host, port)
    print(f"Kapibala Web UI · {application.mode}")
    print(f"http://{host}:{server.server_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
