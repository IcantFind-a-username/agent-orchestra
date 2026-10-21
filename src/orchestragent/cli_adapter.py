"""Adapter for a local command the user already installed and configured.

The command is executed directly (never through a shell), receives one JSON
object on stdin, and must print exactly one JSON object on stdout. Credentials
stay inside the tool: nothing here reads its configuration or environment.
"""

import json
import subprocess
from dataclasses import dataclass
from typing import cast

from orchestragent.adapters import StepContext, StepResult
from orchestragent.schema import validate


@dataclass(frozen=True)
class CliEndpoint:
    """A command template such as `("/usr/local/bin/tool", "--emit-json")`."""

    command: tuple[str, ...]
    timeout: float = 120.0

    def argv(self) -> list[str]:
        return list(self.command)


@dataclass(frozen=True)
class CliAdapter:
    id: str
    endpoint: CliEndpoint

    def run(self, context: StepContext) -> StepResult:
        payload = {
            "stage": context.stage,
            "prompt": context.prompt,
            "schema": context.schema,
            "revision": context.revision,
            "attempt": context.attempt,
        }
        try:
            completed = subprocess.run(
                self.endpoint.argv(),
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                timeout=self.endpoint.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return self._fail("timed_out", "Local command timed out; explicit resume required")
        except OSError:
            return self._fail("failed", "Local command could not be started")

        if completed.returncode != 0:
            # Exit status only: the tool's stderr may carry credentials.
            return self._fail(
                "failed", f"Local command exited with status {completed.returncode}"
            )

        try:
            data = json.loads(completed.stdout)
            if not isinstance(data, dict):
                raise ValueError("Output must be a JSON object")
            validate(data, context.schema)
        except ValueError:
            return self._fail("failed", "Local command output did not match the stage contract")

        summary = data.get("summary")
        return StepResult(
            outcome="ok",
            data=cast(dict[str, object], data),
            summary=summary if isinstance(summary, str) else "",
            provider=self.id,
            model=self.endpoint.command[0],
            cost_usd=None,
        )

    def _fail(self, outcome: str, summary: str) -> StepResult:
        return StepResult(
            outcome=outcome,
            summary=summary,
            provider=self.id,
            model=self.endpoint.command[0] if self.endpoint.command else "",
            cost_usd=None,
        )
