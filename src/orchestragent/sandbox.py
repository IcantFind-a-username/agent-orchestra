"""Process isolation for running tests this project did not write.

A sandbox is optional by design: where none is available the controller refuses
to execute model-authored tests instead of running them unconfined.
"""

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class Sandbox(Protocol):
    name: str

    def available(self) -> bool: ...

    def command(self, argv: list[str], cwd: Path) -> list[str]: ...


@dataclass(frozen=True)
class NoSandbox:
    """Explicitly no isolation; the controller will refuse to run such tests."""

    name = "none"

    def available(self) -> bool:
        return False

    def command(self, argv: list[str], cwd: Path) -> list[str]:
        return list(argv)


@dataclass(frozen=True)
class MacOSSandbox:
    """`sandbox-exec` with a deny-default profile: no network, writes confined to the tree."""

    name = "sandbox-exec"

    def available(self) -> bool:
        return Path("/usr/bin/sandbox-exec").is_file()

    def command(self, argv: list[str], cwd: Path) -> list[str]:
        profile = (
            "(version 1)"
            "(deny default)"
            "(allow process*)"
            "(allow file-read*)"
            "(deny network*)"
            f'(allow file-write* (subpath "{cwd}") (subpath "/private/tmp")'
            ' (literal "/dev/null"))'
        )
        return ["/usr/bin/sandbox-exec", "-p", profile, *argv]


@dataclass(frozen=True)
class LinuxSandbox:
    """bubblewrap: no network, read-only host, writable worktree only."""

    name = "bwrap"

    def available(self) -> bool:
        return shutil.which("bwrap") is not None

    def command(self, argv: list[str], cwd: Path) -> list[str]:
        return [
            "bwrap",
            "--unshare-all",
            "--die-with-parent",
            "--new-session",
            "--ro-bind",
            "/",
            "/",
            "--bind",
            str(cwd),
            str(cwd),
            "--chdir",
            str(cwd),
            "--dev",
            "/dev",
            "--proc",
            "/proc",
            *argv,
        ]


def detect() -> Sandbox:
    """The strongest available backend, or NoSandbox."""
    for candidate in (MacOSSandbox(), LinuxSandbox()):
        if candidate.available():
            return candidate
    return NoSandbox()


def run(sandbox: Sandbox, argv: list[str], cwd: Path, timeout: float) -> tuple[bool, str]:
    """Run under the sandbox; returns (passed, output)."""
    if not sandbox.available():
        raise ValueError("No sandbox backend is available")
    result = subprocess.run(
        sandbox.command(argv, cwd),
        cwd=cwd,
        env={"PATH": "/usr/bin:/bin", "HOME": str(cwd)},
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return result.returncode == 0, (result.stderr or result.stdout).strip()


def python_argv() -> list[str]:
    return [sys.executable, "-I", "-S", "-m", "unittest", "discover", "-q"]
