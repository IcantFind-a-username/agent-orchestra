from typing import cast

import pytest

from orchestragent.settings import SettingsStore
from orchestragent.store import Conflict


def test_default_policy_tracks_registered_providers(settings: SettingsStore) -> None:
    policy = cast(dict[str, object], settings.read()["policy"])
    providers = cast(list[dict[str, object]], policy["providers"])
    assert [provider["id"] for provider in providers] == ["mock"]
    assert policy["scheduler_provider"] == "mock"


def test_update_requires_the_current_revision(settings: SettingsStore) -> None:
    policy = cast(dict[str, object], settings.read()["policy"])
    with pytest.raises(Conflict, match="reload"):
        settings.update(99, policy)


def test_unknown_provider_is_rejected(settings: SettingsStore) -> None:
    policy = cast(dict[str, object], settings.read()["policy"])
    policy["scheduler_provider"] = "unregistered"
    with pytest.raises(ValueError, match="Unknown scheduler provider"):
        settings.update(1, policy)


def test_policy_requires_every_provider_exactly_once(settings: SettingsStore) -> None:
    policy = cast(dict[str, object], settings.read()["policy"])
    policy["providers"] = []
    with pytest.raises(ValueError, match="each registered provider"):
        settings.update(1, policy)


def test_oversized_instructions_are_rejected(settings: SettingsStore) -> None:
    policy = cast(dict[str, object], settings.read()["policy"])
    policy["instructions"] = "x" * 4097
    with pytest.raises(ValueError, match="oversized"):
        settings.update(1, policy)


def test_proposal_is_previewed_before_it_is_applied(settings: SettingsStore) -> None:
    proposed = settings.propose("keep tests fast")
    assert proposed["mode"] == "mock"
    assert "Preview" in cast(str, proposed["summary"])
    # A preview alone must not change the live policy.
    assert settings.read()["revision"] == 1
    applied = settings.apply(cast(str, proposed["id"]))
    assert applied["revision"] == 2
    policy = cast(dict[str, object], applied["policy"])
    assert policy["instructions"] == "keep tests fast"


def test_stale_proposal_cannot_be_applied(settings: SettingsStore) -> None:
    proposed = settings.propose("first")
    current = cast(dict[str, object], settings.read()["policy"])
    settings.update(1, current)
    with pytest.raises(Conflict, match="stale"):
        settings.apply(cast(str, proposed["id"]))


def test_instruction_length_is_bounded(settings: SettingsStore) -> None:
    with pytest.raises(ValueError, match="1 to 2048"):
        settings.propose("")
    with pytest.raises(ValueError, match="1 to 2048"):
        settings.propose("x" * 2049)
