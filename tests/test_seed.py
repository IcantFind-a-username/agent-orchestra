from pathlib import Path

import pytest

from orchestragent.adapters import MockAdapter, StepContext, StepResult
from orchestragent.controller import Controller
from orchestragent.registry import ProviderRegistry
from orchestragent.sandbox import NoSandbox
from orchestragent.search import plan_context
from orchestragent.store import Store
from orchestragent.workspace import Workspace


@pytest.fixture
def seed(tmp_path: Path) -> Path:
    root = tmp_path / "seed"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "clamp.py").write_text("def clamp(x, lo, hi):\n    return x\n")
    (root / "README.md").write_text("# existing project\n")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("NEEDLE in git\n")
    return root


def test_a_seeded_workspace_starts_from_the_existing_tree(seed: Path, tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "w", seed=seed)
    _, implementation = workspace.prepare("a" * 32)
    assert (implementation / "pkg" / "clamp.py").read_text().startswith("def clamp")
    assert (implementation / "README.md").read_text() == "# existing project\n"
    # .git is never copied; the worktree gets its own pointer file.
    assert not (implementation / ".git" / "config").exists()


def test_an_unseeded_workspace_keeps_the_placeholder(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "w")
    _, implementation = workspace.prepare("b" * 32)
    assert (implementation / "README.md").read_text() == "Isolated task workspace.\n"


def test_a_missing_seed_directory_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must be a directory"):
        Workspace(tmp_path / "w", seed=tmp_path / "nope")


def test_plan_context_carries_files_and_hits(seed: Path) -> None:
    context = plan_context(seed, "tighten clamp bounds")
    assert "pkg/clamp.py" in context["files"]
    hits = context["hits"]
    assert isinstance(hits, list) and hits
    assert hits[0]["path"] == "pkg/clamp.py"
    assert context["query"] == ["tighten", "clamp", "bounds"]


def test_plan_context_skips_vcs_noise(seed: Path) -> None:
    context = plan_context(seed, "NEEDLE")
    assert all(not hit["path"].startswith(".git") for hit in context["hits"])


class Capture(MockAdapter):
    id = "capture"

    def __init__(self) -> None:
        self.seen: dict[str, object] = {}

    def run(self, context: StepContext) -> StepResult:
        if context.stage == "plan":
            self.seen = context.context_map
        return super().run(context)


def test_the_plan_stage_receives_the_context_map(seed: Path, tmp_path: Path) -> None:
    adapter = Capture()
    controller = Controller(
        Store(tmp_path / "t.sqlite"),
        Workspace(tmp_path / "w", seed=seed),
        registry=ProviderRegistry([adapter]),
        routes={stage: "capture" for stage in ("plan", "implement", "review", "test", "summarize")},
        sandbox=NoSandbox(),
    )
    controller.submit("seeded", "tighten clamp bounds")
    controller.tick()
    assert adapter.seen["files"]
    assert adapter.seen["hits"]
