import sys
from pathlib import Path

import pytest

from orchestragent.sandbox import NoSandbox, detect, run

SANDBOX = detect()
pytestmark = pytest.mark.skipif(not SANDBOX.available(), reason="no sandbox backend here")


def run_code(cwd: Path, code: str) -> tuple[bool, str]:
    return run(SANDBOX, [sys.executable, "-I", "-S", "-c", code], cwd, timeout=30)


def test_no_sandbox_is_never_usable() -> None:
    assert NoSandbox().available() is False


def test_network_is_denied(tmp_path: Path) -> None:
    passed, _ = run_code(
        tmp_path, "import socket; socket.create_connection(('1.1.1.1', 80), timeout=2)"
    )
    assert passed is False


def test_writes_outside_the_tree_are_denied(tmp_path: Path) -> None:
    escape = tmp_path.parent / "sandbox-escape.txt"
    passed, output = run_code(tmp_path, f"open({str(escape)!r}, 'w').write('x')")
    assert passed is False, output
    assert not escape.exists()


def test_writes_inside_the_tree_are_allowed(tmp_path: Path) -> None:
    passed, output = run_code(tmp_path, "open('inside.txt', 'w').write('ok')")
    assert passed is True, output
    assert (tmp_path / "inside.txt").read_text() == "ok"
