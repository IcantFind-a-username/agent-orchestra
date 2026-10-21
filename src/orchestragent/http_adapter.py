"""A generic JSON-over-HTTP adapter.

Deliberately provider-agnostic: the endpoint, model and credential come from
the caller's configuration, and the reply must satisfy the stage schema.
No provider name appears in this module.
"""

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import cast

from orchestragent.adapters import StepContext, StepResult
from orchestragent.schema import validate


@dataclass(frozen=True)
class HttpEndpoint:
    """Everything needed to reach one chat-completions style endpoint."""

    base_url: str
    model: str
    api_key: str = ""
    path: str = "/chat/completions"
    timeout: float = 120.0

    def url(self) -> str:
        return self.base_url.rstrip("/") + "/" + self.path.lstrip("/")


@dataclass(frozen=True)
class HttpAdapter:
    """Calls a JSON chat endpoint and validates the reply against the schema."""

    id: str
    endpoint: HttpEndpoint
    extra_headers: dict[str, str] = field(default_factory=dict)

    def run(self, context: StepContext) -> StepResult:
        try:
            status, raw = self._post(self._body(context))
        except TimeoutError:
            return self._fail("timed_out", "Endpoint timed out; explicit resume required")
        except OSError:
            return self._fail("failed", "Endpoint unreachable; no fallback was attempted")

        if status in (401, 403):
            return self._fail("auth_required", "Endpoint rejected the credential")
        if status == 429:
            return self._fail("rate_limited", "Endpoint rate limited this request", retry_after=1.0)
        if status >= 400:
            return self._fail("failed", f"Endpoint returned HTTP {status}")

        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("Reply must be a JSON object")
            data = self._extract(payload)
            validate(data, context.schema)
        except ValueError:
            # Never echo the raw body: it may carry the endpoint owner's data.
            return self._fail("failed", "Endpoint reply did not match the stage contract")

        summary = data.get("summary")
        usage = payload.get("usage")
        return StepResult(
            outcome="ok",
            data=data,
            summary=summary if isinstance(summary, str) else "",
            provider=self.id,
            model=self.endpoint.model,
            cost_usd=None,
            usage=cast(dict[str, object], usage) if isinstance(usage, dict) else {},
        )

    def _fail(self, outcome: str, summary: str, retry_after: float = 0.0) -> StepResult:
        return StepResult(
            outcome=outcome,
            summary=summary,
            provider=self.id,
            model=self.endpoint.model,
            retry_after=retry_after,
            cost_usd=None,
        )

    def _body(self, context: StepContext) -> dict[str, object]:
        instruction = (
            f"Stage: {context.stage}. "
            "Reply with a single JSON object that satisfies the provided schema. "
            "No prose, no code fences."
        )
        return {
            "model": self.endpoint.model,
            "messages": [
                {"role": "system", "content": instruction},
                {"role": "user", "content": context.prompt},
            ],
            "response_format": {"type": "json_object"},
        }

    def _post(self, body: dict[str, object]) -> tuple[int, bytes]:
        headers = {"Content-Type": "application/json", **self.extra_headers}
        if self.endpoint.api_key:
            headers["Authorization"] = f"Bearer {self.endpoint.api_key}"
        request = urllib.request.Request(
            self.endpoint.url(),
            data=json.dumps(body).encode(),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.endpoint.timeout) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            # 4xx/5xx arrive as exceptions; the status must survive for classification.
            error.close()
            return error.code, b""

    @staticmethod
    def _extract(payload: dict[str, object]) -> dict[str, object]:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ValueError("Missing choices")
        first = choices[0]
        if not isinstance(first, dict):
            raise ValueError("Malformed choice")
        message = first.get("message")
        if not isinstance(message, dict):
            raise ValueError("Malformed message")
        content = message.get("content")
        if not isinstance(content, str):
            raise ValueError("Missing content")
        value = json.loads(content)
        if not isinstance(value, dict):
            raise ValueError("Content must be a JSON object")
        return cast(dict[str, object], value)
