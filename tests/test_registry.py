import pytest

from orchestragent.adapters import MockAdapter, ProviderDisabledError, StepContext, StepResult
from orchestragent.registry import ProviderRegistry


class Nameless:
    id = ""

    def run(self, context: StepContext) -> StepResult:
        raise AssertionError("must not run")


@pytest.fixture
def context() -> StepContext:
    return StepContext("task", "test", "implement", "happy", 1, "mock", "", "")


def test_registry_runs_the_registered_adapter(context: StepContext) -> None:
    assert ProviderRegistry([MockAdapter()]).run("mock", context) == MockAdapter().run(context)


def test_unknown_provider_does_not_fall_back(context: StepContext) -> None:
    registry = ProviderRegistry([MockAdapter()])
    with pytest.raises(ProviderDisabledError, match="no fallback"):
        registry.run("unexpected", context)


def test_duplicate_ids_are_rejected() -> None:
    with pytest.raises(ValueError, match="unique"):
        ProviderRegistry([MockAdapter(), MockAdapter()])


def test_empty_id_is_rejected() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        ProviderRegistry([Nameless()])
