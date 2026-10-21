from pathlib import Path

import pytest

from orchestragent.adapters import MockAdapter, StepContext, StepResult
from orchestragent.controller import Controller
from orchestragent.registry import ProviderRegistry
from orchestragent.sandbox import NoSandbox, Sandbox, detect
from orchestragent.store import Store
from orchestragent.workspace import Workspace

FILES = [
    {"path": "calc.py", "content": "def add(a: int, b: int) -> int:\n    return a + b\n"},
    {
        "path": "test_calc.py",
        "content": (
            "import unittest\n\nfrom calc import add\n\n\n"
            "class Calc(unittest.TestCase):\n"
            "    def test_add(self) -> None:\n"
            "        self.assertEqual(add(2, 3), 5)\n"
        ),
    },
]


class BundleAdapter(MockAdapter):
    id = "bundle"

    def run(self, context: StepContext) -> StepResult:
        if context.stage == "implement":
            return StepResult(data={"files": FILES, "summary": "two files"}, provider="bundle")
        if context.stage == "review":
            assert context.artifact, "review must receive the pinned bundle"
            return StepResult(summary="reviewed the pinned revision", provider="bundle")
        return StepResult(summary=f"{context.stage} ok", provider="bundle")


def build(tmp_path: Path, allow: bool = False, sandbox: Sandbox | None = None) -> Controller:
    return Controller(
        Store(tmp_path / "tasks.sqlite"),
        Workspace(tmp_path / "workspaces"),
        registry=ProviderRegistry([BundleAdapter()]),
        routes={stage: "bundle" for stage in ("plan", "implement", "review", "test", "summarize")},
        allow_bundled_tests=allow,
        sandbox=sandbox or NoSandbox(),
    )


@pytest.mark.parametrize(
    ("files", "reason"),
    [
        ([], "at least one file"),
        ([{"path": "/etc/passwd", "content": ""}], "must be relative"),
        ([{"path": "../escape.py", "content": ""}], "must not escape"),
        ([{"path": ".hidden.py", "content": ""}], "must not escape or hide"),
        ([{"path": "hook.sh", "content": ""}], "Disallowed file extension"),
        ([{"path": "a.py", "content": ""}, {"path": "a.py", "content": ""}], "Duplicate"),
        ([{"path": "a.py", "content": "x" * 200_000}], "size limit"),
        ([{"path": "a.py", "content": "", "mode": "755"}], "exactly path and content"),
    ],
)
def test_bundles_that_could_escape_or_smuggle_are_rejected(
    tmp_path: Path, files: object, reason: str
) -> None:
    with pytest.raises(ValueError, match=reason):
        Workspace(tmp_path / "w").validate_files(files)


def test_a_bundle_is_written_and_committed_as_one_revision(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "w")
    revision, diff = workspace.implement_bundle("a" * 32, FILES)
    assert len(revision) == 40
    assert "calc.py" in diff
    assert '"test_calc.py"' in workspace.bundle_at("a" * 32, revision)


def test_tests_are_not_executed_without_an_explicit_opt_in(tmp_path: Path) -> None:
    controller = build(tmp_path)
    task, _ = controller.submit("b1", "add two numbers")
    while controller.tick():
        pass
    finished = controller.store.read(task.id)
    assert (finished.status, finished.stage) == ("policy_blocked", "test")
    assert "No sandbox backend" in finished.error
    assert {item.name for item in finished.artifacts} >= {"calc.py", "test_calc.py", "bundle.json"}


def test_the_opt_in_runs_the_bundled_tests(tmp_path: Path) -> None:
    controller = build(tmp_path, allow=True)
    task, _ = controller.submit("b2", "add two numbers")
    while controller.tick():
        pass
    finished = controller.store.read(task.id)
    assert finished.status == "completed"
    report = next(
        item.content for item in finished.artifacts if item.name == "integration-tests.txt"
    )
    assert "PASS" in report
    assert "UNSANDBOXED" in report


SANDBOX = detect()


@pytest.mark.skipif(not SANDBOX.available(), reason="no sandbox backend on this platform")
def test_a_real_sandbox_runs_the_bundled_tests(tmp_path: Path) -> None:
    controller = build(tmp_path, sandbox=SANDBOX)
    task, _ = controller.submit("b3", "add two numbers")
    while controller.tick():
        pass
    finished = controller.store.read(task.id)
    assert finished.status == "completed"
    report = next(
        item.content for item in finished.artifacts if item.name == "integration-tests.txt"
    )
    assert SANDBOX.name in report
    assert "PASS" in report
