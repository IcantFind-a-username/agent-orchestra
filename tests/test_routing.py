from pathlib import Path

import pytest

from orchestragent.adapters import MockAdapter, StepContext, StepResult
from orchestragent.controller import Controller
from orchestragent.registry import ProviderRegistry
from orchestragent.store import Store
from orchestragent.workspace import Workspace


class Recorder(MockAdapter):
    def __init__(self, name: str) -> None:
        self.id = name
        self.calls: list[str] = []

    def run(self, context: StepContext) -> StepResult:
        self.calls.append(context.stage)
        return super().run(context)


def build(tmp_path: Path, routes: dict[str, str]) -> tuple[Controller, Recorder, Recorder]:
    implementing, reviewing = Recorder("implement"), Recorder("review")
    controller = Controller(
        Store(tmp_path / "tasks.sqlite"),
        Workspace(tmp_path / "workspaces"),
        registry=ProviderRegistry([implementing, reviewing, MockAdapter()]),
        routes=routes,
    )
    return controller, implementing, reviewing


def test_implementation_and_review_cannot_share_an_endpoint(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="different providers"):
        build(
            tmp_path,
            {"plan": "implement", "implement": "implement", "review": "implement"},
        )


def test_a_route_may_not_name_an_unregistered_provider(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unregistered provider"):
        build(tmp_path, {"plan": "ghost", "implement": "implement", "review": "review"})


def test_each_stage_runs_on_its_configured_endpoint(tmp_path: Path) -> None:
    controller, implementing, reviewing = build(
        tmp_path,
        {"plan": "implement", "implement": "implement", "review": "review"},
    )
    task, _ = controller.submit("routed", "write a clamp function")
    while controller.tick():
        pass
    finished = controller.store.read(task.id)
    assert finished.status == "completed"
    assert implementing.calls == ["plan", "implement"]
    assert reviewing.calls == ["review"]
    assert [step.provider for step in finished.steps] == [
        "implement",
        "implement",
        "review",
        "mock",
        "mock",
    ]
