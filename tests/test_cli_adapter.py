import sys
import textwrap
from pathlib import Path

import pytest

from orchestragent.adapters import StepContext
from orchestragent.cli_adapter import CliAdapter, CliEndpoint
from orchestragent.schema import object_schema

SCHEMA = object_schema({"summary": {"type": "string"}})


def script(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "tool.py"
    path.write_text(textwrap.dedent(body))
    return path


def adapter(path: Path, timeout: float = 10.0) -> CliAdapter:
    return CliAdapter("local", CliEndpoint(command=(sys.executable, str(path)), timeout=timeout))


def context() -> StepContext:
    return StepContext(
        task_id="t",
        prompt="do it",
        stage="implement",
        scenario="happy",
        attempt=1,
        session_id="s",
        revision="",
        artifact="",
        schema=SCHEMA,
    )


def test_the_command_receives_the_request_on_stdin_and_returns_data(tmp_path: Path) -> None:
    tool = script(
        tmp_path,
        """
        import json, sys
        request = json.load(sys.stdin)
        assert request["stage"] == "implement"
        assert request["prompt"] == "do it"
        assert request["schema"]["required"] == ["summary"]
        print(json.dumps({"summary": "from the tool"}))
        """,
    )
    result = adapter(tool).run(context())
    assert (result.outcome, result.data, result.summary) == (
        "ok",
        {"summary": "from the tool"},
        "from the tool",
    )
    assert (result.provider, result.cost_usd) == ("local", None)


def test_a_nonzero_exit_reports_the_status_but_not_the_stderr(tmp_path: Path) -> None:
    tool = script(
        tmp_path,
        """
        import sys
        sys.stderr.write("SECRET-TOKEN-123\\n")
        raise SystemExit(3)
        """,
    )
    result = adapter(tool).run(context())
    assert result.outcome == "failed"
    assert "status 3" in result.summary
    assert "SECRET-TOKEN-123" not in result.summary


def test_a_timeout_is_classified(tmp_path: Path) -> None:
    tool = script(tmp_path, "import time; time.sleep(5)")
    result = adapter(tool, timeout=0.2).run(context())
    assert result.outcome == "timed_out"


@pytest.mark.parametrize(
    "body",
    [
        "print('not json')",
        "print('[1, 2, 3]')",
        "print('{\"summary\": 5}')",
    ],
)
def test_output_must_satisfy_the_contract(tmp_path: Path, body: str) -> None:
    tool = script(tmp_path, body)
    result = adapter(tool).run(context())
    assert result.outcome == "failed"
    assert "did not match" in result.summary


def test_a_missing_executable_fails_closed(tmp_path: Path) -> None:
    result = CliAdapter("local", CliEndpoint(("/nonexistent/tool",))).run(context())
    assert result.outcome == "failed"
