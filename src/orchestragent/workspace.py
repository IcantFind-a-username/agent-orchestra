"""Git isolation for one task, plus the fixed Mock fixture.

A worktree is not a security sandbox on its own; see SECURITY.md.
"""

import ast
import json
import os
import subprocess
import sys
from pathlib import Path, PurePosixPath

GOOD_SOURCE = "def add(a: int, b: int) -> int:\n    return a + b\n"
BAD_SOURCE = "def add(a: int, b: int) -> int:\n    return a - b\n"


class Workspace:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def git(cwd: Path, *args: str, strip: bool = True) -> str:
        # Do not inherit git hooks, external diff tools, SSH agent, or API credentials.
        env = {
            "PATH": os.defpath,
            "HOME": str(cwd),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
            "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
        }
        result = subprocess.run(
            [
                "git",
                "-c",
                "user.name=agent-orchestra Mock",
                "-c",
                "user.email=mock@localhost",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "commit.gpgsign=false",
                *args,
            ],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        return result.stdout.strip() if strip else result.stdout

    def repo(self, task_id: str) -> Path:
        # Task ids originate in uuid4; validate again at the filesystem boundary.
        if len(task_id) != 32 or any(c not in "0123456789abcdef" for c in task_id):
            raise ValueError("Invalid workspace id")
        return self.root / task_id

    def implement(self, task_id: str, source: str) -> tuple[str, str]:
        if ast.dump(ast.parse(source)) not in {
            ast.dump(ast.parse(GOOD_SOURCE)),
            ast.dump(ast.parse(BAD_SOURCE)),
        }:
            raise ValueError("Mock workspace accepts only the fixed arithmetic fixture")
        repo, implementation = self.prepare(task_id)
        (implementation / "calculator.py").write_text(source)
        self.git(implementation, "add", "calculator.py")
        if self.git(implementation, "diff", "--cached", "--name-only"):
            self.git(implementation, "commit", "-m", "Mock implementation")
        revision = self.git(implementation, "rev-parse", "HEAD")
        diff = self.git(repo, "diff", "main", revision, "--", "calculator.py")
        return revision, diff

    def prepare(self, task_id: str) -> tuple[Path, Path]:
        """Shared replay-safe seed and isolated implementation worktree."""
        repo = self.repo(task_id)
        repo.mkdir(exist_ok=True)
        if not (repo / ".git").exists():
            self.git(repo, "init", "-b", "main")
        try:
            self.git(repo, "rev-parse", "--verify", "HEAD")
        except subprocess.CalledProcessError:
            # A prior process may have died after writing/staging but before committing.
            (repo / "README.md").write_text("Isolated task workspace.\n")
            self.git(repo, "add", "README.md")
            self.git(repo, "commit", "-m", "Seed Mock workspace")
        implementation = repo / "implementation"
        if not implementation.exists():
            self.git(
                repo, "worktree", "add", "-B", "mock/implementation", str(implementation), "main"
            )
        return repo, implementation

    def review_source(self, task_id: str, revision: str) -> str:
        repo = self.repo(task_id)
        if len(revision) != 40 or any(c not in "0123456789abcdef" for c in revision):
            raise ValueError("Review requires a fixed Git revision")
        review = repo / "review"
        if not review.exists():
            self.git(repo, "worktree", "add", "--detach", str(review), revision)
        if self.git(review, "rev-parse", "HEAD") != revision:
            raise ValueError("Review revision changed")
        return self.git(review, "show", f"{revision}:calculator.py") + "\n"

    def integrate_and_test(self, task_id: str, revision: str) -> tuple[bool, str]:
        repo = self.repo(task_id)
        self.git(repo, "merge", "--ff-only", revision)
        if self.git(repo, "rev-parse", "HEAD") != revision:
            raise ValueError("Integrated revision differs from reviewed revision")
        source = self.git(repo, "show", "HEAD:calculator.py") + "\n"
        # Fixed fixture runner avoids executing arbitrary code, even from a corrupt artifact.
        syntax = ast.dump(ast.parse(source))
        if syntax not in {ast.dump(ast.parse(GOOD_SOURCE)), ast.dump(ast.parse(BAD_SOURCE))}:
            raise ValueError("Refusing non-fixture code")
        runner = source + "\nassert add(2, 3) == 5\nassert add(-2, 2) == 0\n"
        # Execute the exact validated blob, never import the mutable worktree file.
        # -I excludes cwd, PYTHONPATH and user packages; -S disables site initialization.
        result = subprocess.run(
            [sys.executable, "-I", "-S", "-c", runner],
            cwd=repo,
            env={"PATH": os.defpath, "HOME": str(repo)},
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        passed = result.returncode == 0
        return passed, f"Mock fixed-fixture integration tests: {'PASS 2/2' if passed else 'FAIL'}"

    # --- generic bundles, for adapters that return real files ---

    ALLOWED_SUFFIXES = frozenset({".py", ".md", ".txt", ".json", ".toml", ".cfg", ".ini"})
    MAX_FILES = 20
    MAX_BYTES = 180_000

    def validate_files(self, files: object) -> list[dict[str, str]]:
        """Reject a bundle that could escape the worktree or smuggle in hooks."""
        if not isinstance(files, list) or not files:
            raise ValueError("A bundle must contain at least one file")
        if len(files) > self.MAX_FILES:
            raise ValueError("Bundle exceeds the file count limit")
        accepted: dict[str, str] = {}
        total = 0
        for item in files:
            if not isinstance(item, dict) or set(item) != {"path", "content"}:
                raise ValueError("Each file requires exactly path and content")
            path, content = item["path"], item["content"]
            if not isinstance(path, str) or not isinstance(content, str):
                raise ValueError("File path and content must be strings")
            candidate = PurePosixPath(path)
            if candidate.is_absolute() or not candidate.parts:
                raise ValueError("File paths must be relative")
            if any(part == ".." or part.startswith(".") for part in candidate.parts):
                raise ValueError("File paths must not escape or hide")
            if candidate.suffix not in self.ALLOWED_SUFFIXES:
                raise ValueError(f"Disallowed file extension: {candidate.suffix}")
            if path in accepted:
                raise ValueError("Duplicate file path")
            total += len(content.encode("utf-8"))
            accepted[path] = content
        if total > self.MAX_BYTES:
            raise ValueError("Bundle exceeds the size limit")
        return [{"path": path, "content": accepted[path]} for path in sorted(accepted)]

    def implement_bundle(self, task_id: str, files: object) -> tuple[str, str]:
        accepted = self.validate_files(files)
        repo, implementation = self.prepare(task_id)
        for item in accepted:
            target = implementation / item["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(item["content"])
        self.git(implementation, "add", "-A")
        if self.git(implementation, "diff", "--cached", "--name-only"):
            self.git(implementation, "commit", "-m", "Implement stage output")
        revision = self.git(implementation, "rev-parse", "HEAD")
        return revision, self.git(repo, "diff", "main", revision)

    def bundle_at(self, task_id: str, revision: str) -> str:
        """The exact files the implementation changed, read at a fixed revision."""
        repo = self.repo(task_id)
        if len(revision) != 40 or any(c not in "0123456789abcdef" for c in revision):
            raise ValueError("Review requires a fixed Git revision")
        names = sorted(self.git(repo, "diff", "--name-only", "main", revision).splitlines())
        files = [
            {"path": name, "content": self.git(repo, "show", f"{revision}:{name}", strip=False)}
            for name in names
            if name
        ]
        return json.dumps(files, ensure_ascii=False, sort_keys=True)

    def integrate_bundle_and_test(self, task_id: str, revision: str) -> tuple[bool, str]:
        """Merge and run the project's own tests. Only reachable with explicit opt-in."""
        repo = self.repo(task_id)
        self.git(repo, "merge", "--ff-only", revision)
        if self.git(repo, "rev-parse", "HEAD") != revision:
            raise ValueError("Integrated revision differs from reviewed revision")
        result = subprocess.run(
            [sys.executable, "-I", "-S", "-m", "unittest", "discover", "-q"],
            cwd=repo,
            env={"PATH": os.defpath, "HOME": str(repo)},
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        passed = result.returncode == 0
        output = (result.stderr or result.stdout).strip()
        return passed, f"Bundled tests: {'PASS' if passed else 'FAIL'}\n{output[-2000:]}"
