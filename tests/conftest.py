from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest

from orchestragent.controller import Controller
from orchestragent.settings import SettingsStore
from orchestragent.store import Store
from orchestragent.workspace import Workspace


@dataclass
class Clock:
    now: float = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def controller(tmp_path: Path, clock: Clock) -> Controller:
    return Controller(
        Store(tmp_path / "tasks.sqlite"), Workspace(tmp_path / "workspaces"), clock=clock
    )


@pytest.fixture
def settings(tmp_path: Path) -> SettingsStore:
    return SettingsStore(Store(tmp_path / "settings.db"))


@pytest.fixture
def policy(settings: SettingsStore) -> dict[str, object]:
    return cast(dict[str, object], settings.read()["policy"])


@pytest.fixture
def providers(policy: dict[str, object]) -> list[dict[str, object]]:
    return cast(list[dict[str, object]], policy["providers"])
