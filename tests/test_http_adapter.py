import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from orchestragent.adapters import StepContext
from orchestragent.http_adapter import HttpAdapter, HttpEndpoint
from orchestragent.schema import object_schema

SCHEMA = object_schema({"summary": {"type": "string"}})


def completion(content: object) -> dict[str, object]:
    return {
        "choices": [{"message": {"content": json.dumps(content)}}],
        "usage": {"total_tokens": 5},
    }


class Handler(BaseHTTPRequestHandler):
    status = 200
    payload: object = completion({"summary": "ok"})
    seen: dict[str, object] = {}

    def log_message(self, format: str, *args: object) -> None:
        pass

    def do_POST(self) -> None:
        size = int(self.headers.get("Content-Length", "0"))
        Handler.seen = {
            "body": json.loads(self.rfile.read(size)),
            "auth": self.headers.get("Authorization"),
        }
        body = json.dumps(Handler.payload).encode()
        self.send_response(Handler.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def endpoint() -> Iterator[str]:
    Handler.status, Handler.payload, Handler.seen = 200, completion({"summary": "ok"}), {}
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def context(prompt: str = "do the thing") -> StepContext:
    return StepContext(
        task_id="task",
        prompt=prompt,
        stage="implement",
        scenario="happy",
        attempt=1,
        session_id="s",
        revision="",
        artifact="",
        schema=SCHEMA,
    )


def adapter(base_url: str, api_key: str = "") -> HttpAdapter:
    return HttpAdapter("endpoint", HttpEndpoint(base_url=base_url, model="m", api_key=api_key))


def test_successful_reply_is_validated_and_marked_unknown_cost(endpoint: str) -> None:
    result = adapter(endpoint).run(context())
    assert (result.outcome, result.data, result.summary) == ("ok", {"summary": "ok"}, "ok")
    assert (result.provider, result.model, result.cost_usd) == ("endpoint", "m", None)
    assert result.usage == {"total_tokens": 5}


def test_the_request_carries_the_model_and_a_json_object_hint(endpoint: str) -> None:
    adapter(endpoint).run(context("rewrite this"))
    body = Handler.seen["body"]
    assert isinstance(body, dict)
    assert body["model"] == "m"
    assert body["response_format"] == {"type": "json_object"}
    assert body["messages"][1]["content"] == "rewrite this"


def test_credential_is_sent_only_when_configured(endpoint: str) -> None:
    adapter(endpoint).run(context())
    assert Handler.seen["auth"] is None
    adapter(endpoint, api_key="secret").run(context())
    assert Handler.seen["auth"] == "Bearer secret"


@pytest.mark.parametrize(
    ("status", "outcome", "retry_after"),
    [(401, "auth_required", 0.0), (403, "auth_required", 0.0), (429, "rate_limited", 1.0),
     (500, "failed", 0.0)],
)
def test_http_status_classification(
    endpoint: str, status: int, outcome: str, retry_after: float
) -> None:
    Handler.status = status
    result = adapter(endpoint).run(context())
    assert (result.outcome, result.retry_after, result.cost_usd) == (outcome, retry_after, None)


@pytest.mark.parametrize(
    "payload",
    [
        {"choices": []},
        {"choices": [{"message": {"content": "not json"}}]},
        {"choices": [{"message": {"content": json.dumps({"summary": 1})}}]},
        {"choices": [{"message": {"content": json.dumps(["not", "object"])}}]},
    ],
)
def test_malformed_or_off_contract_replies_fail_closed(endpoint: str, payload: object) -> None:
    Handler.payload = payload
    result = adapter(endpoint).run(context())
    assert result.outcome == "failed"
    assert "did not match" in result.summary


def test_unreachable_endpoint_does_not_fall_back() -> None:
    result = adapter("http://127.0.0.1:1").run(context())
    assert (result.outcome, result.cost_usd) == ("failed", None)
