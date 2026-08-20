"""Local Web UI and JSON API for the lead-screening demo."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from threading import RLock, Timer
from urllib.parse import parse_qs, urlparse

from kapibala.agent import ProcessResult, ScreeningAgent
from kapibala.adapters.base import LLMError
from kapibala.adapters.fake import FakeAdapter
from kapibala.audit import AuditLog
from kapibala.debounce import ReplyIntervalBuffer
from kapibala.executor import Executor
from kapibala.followup import FollowupQueue
from kapibala.human_handoff import is_explicit_human_request
from kapibala.rate_limiter import SlidingWindowRateLimiter
from kapibala.reply_generator import TemplateReplyGenerator
from kapibala.runtime import InputValidationError
from kapibala.schemas import Estimation, Intent
from kapibala.state_machine import SessionState, StateMachine

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
    message_buffer: ReplyIntervalBuffer | None = None
    auto_flush: bool = True

    def __post_init__(self) -> None:
        self._lock = RLock()
        self._buffer_timers: dict[str, Timer] = {}
        if self.message_buffer is None:
            self.message_buffer = ReplyIntervalBuffer(
                self.agent.handle_message,
                self.agent.reply_wait_seconds,
                clock=self.clock,
            )

    @property
    def is_fake(self) -> bool:
        return isinstance(self.adapter, FakeAdapter)

    def meta(self) -> dict[str, object]:
        return {"mode": self.mode, "fake": self.is_fake}

    def process_message(self, payload: dict[str, object]) -> dict[str, object]:
        with self._lock:
            raw_customer_id = payload.get("customer_id")
            raw_message = payload.get("message")
            try:
                inbound = self.agent.validate_message(raw_customer_id, raw_message)
            except InputValidationError:
                result = self.agent.handle_message(raw_customer_id, raw_message)
                customer_id = self._normalized_id(raw_customer_id, required=False)
                return {
                    "result": self._serialize_result(result),
                    "aggregation": self._aggregation_status(customer_id),
                    "customer": None,
                }

            customer_id = inbound.customer_id
            session = self.state_machine.get(customer_id).session
            if session is not SessionState.ACTIVE:
                self._clear_buffer_locked(customer_id)
                result = self.agent.handle_message(customer_id, inbound.message)
                submission = None
            else:
                submission = self.message_buffer.submit(
                    customer_id,
                    inbound.message,
                    force=is_explicit_human_request(inbound.message),
                )
                if submission.buffered:
                    self.audit.record(
                        customer_id,
                        "message_buffered",
                        f"pending={submission.pending_count}",
                    )
                    self._schedule_buffer_flush_locked(
                        customer_id, submission.due_in_seconds
                    )
                    result = ProcessResult(note="buffered")
                else:
                    assert isinstance(submission.result, ProcessResult)
                    result = submission.result

            if result.transition.escalated_now or result.transition.closed_now:
                self._clear_buffer_locked(customer_id)
            return {
                "result": self._serialize_result(result),
                "aggregation": (
                    {
                        "buffered": submission.buffered,
                        "pending_count": submission.pending_count,
                        "due_in_seconds": submission.due_in_seconds,
                    }
                    if submission is not None
                    else self._aggregation_status(customer_id)
                ),
                "customer": (
                    self.customer(customer_id)
                    if customer_id and result.note != "invalid_input"
                    else None
                ),
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

    def reset_session(self, payload: dict[str, object]) -> dict[str, object]:
        """Clear all per-customer demo state and return a fresh active session."""
        customer_id = self._normalized_id(payload.get("customer_id"))
        with self._lock:
            self._clear_buffer_locked(customer_id)
            self.agent.reset_session(customer_id)
            if self.is_fake:
                self.adapter.clear_script()
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
                followup_requested = payload.get("followup_requested", False)
                if not isinstance(followup_requested, bool):
                    raise ValueError("followup_requested must be boolean")
                estimation = Estimation(
                    intent=Intent(payload.get("intent", Intent.OTHER.value)),
                    dissatisfied=dissatisfied,
                    followup_requested=followup_requested,
                )
            except (TypeError, ValueError) as exc:
                raise WebRequestError(str(exc)) from exc
            self.adapter.script(estimation)
            return {
                "queued": {
                    "intent": estimation.intent.value,
                    "dissatisfied": estimation.dissatisfied,
                    "followup_requested": estimation.followup_requested,
                }
            }

    def customer(self, customer_id: object) -> dict[str, object]:
        normalized_id = self._normalized_id(customer_id)
        with self._lock:
            state = self.state_machine.get(normalized_id)
            history = self.agent.conversation_store.get(normalized_id)
            buffered_messages = self.message_buffer.pending_messages(normalized_id)
            return {
                "customer_id": normalized_id,
                "state": {
                    "session": state.session.value,
                    "anomaly_count": state.anomaly_count,
                    "last_estimation": (
                        {
                            "intent": state.last_estimation.intent.value,
                            "dissatisfied": state.last_estimation.dissatisfied,
                            "followup_requested": (
                                state.last_estimation.followup_requested
                            ),
                        }
                        if state.last_estimation is not None
                        else None
                    ),
                },
                "history": [
                    {"role": turn.role.value, "content": turn.content}
                    for turn in history
                ],
                "buffered_messages": [
                    {"role": "customer", "content": message}
                    for message in buffered_messages
                ],
                "reply_buffer": self._aggregation_status(normalized_id),
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

    def flush_due_messages(self) -> list[dict[str, object]]:
        """Synchronously flush due reply-interval batches (test/demo hook)."""
        with self._lock:
            outcomes = []
            for customer_id, result in self.message_buffer.flush_due():
                self.audit.record(customer_id, "message_batch_flushed")
                outcomes.append(
                    {
                        "customer_id": customer_id,
                        "result": self._serialize_result(result),
                    }
                )
            return outcomes

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

    def _aggregation_status(self, customer_id: str) -> dict[str, object]:
        if not customer_id:
            return {
                "buffered": False,
                "pending_count": 0,
                "due_in_seconds": 0.0,
            }
        pending_count = self.message_buffer.pending_count(customer_id)
        return {
            "buffered": pending_count > 0,
            "pending_count": pending_count,
            "due_in_seconds": self.message_buffer.due_in(customer_id),
        }

    def _schedule_buffer_flush_locked(
        self, customer_id: str, due_in_seconds: float
    ) -> None:
        if not self.auto_flush:
            return
        old_timer = self._buffer_timers.pop(customer_id, None)
        if old_timer is not None:
            old_timer.cancel()
        timer = Timer(
            max(0.001, due_in_seconds),
            self._flush_buffered_customer,
            args=(customer_id,),
        )
        timer.daemon = True
        timer.start()
        self._buffer_timers[customer_id] = timer

    def _flush_buffered_customer(self, customer_id: str) -> None:
        with self._lock:
            self._buffer_timers.pop(customer_id, None)
            try:
                flushed = self.message_buffer.flush_customer(customer_id)
            except Exception as exc:
                self.audit.record(
                    customer_id, "message_batch_flush_error", type(exc).__name__
                )
                return
            if flushed is not None:
                self.audit.record(customer_id, "message_batch_flushed")
            elif self.message_buffer.pending_count(customer_id):
                self._schedule_buffer_flush_locked(
                    customer_id, self.message_buffer.due_in(customer_id)
                )

    def _clear_buffer_locked(self, customer_id: str) -> None:
        timer = self._buffer_timers.pop(customer_id, None)
        if timer is not None:
            timer.cancel()
        self.message_buffer.reset(customer_id)

    @staticmethod
    def _serialize_result(result: ProcessResult) -> dict[str, object]:
        return {
            "note": result.note,
            "input_error": result.input_error,
            "estimation": (
                {
                    "intent": result.estimation.intent.value,
                    "dissatisfied": result.estimation.dissatisfied,
                    "followup_requested": result.estimation.followup_requested,
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
                    "/api/followups/run": lambda _payload: application.run_followups(),
                    "/api/reactivate": application.reactivate,
                    "/api/reset": application.reset_session,
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
