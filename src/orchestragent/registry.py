"""The single place where adapter implementations are known.

The core holds a registry, never a vendor name. Adding a provider means
registering an adapter here at assembly time, not editing the core.
"""

from collections.abc import Iterable

from orchestragent.adapters import Adapter, ProviderDisabledError, StepContext, StepResult


class ProviderRegistry:
    def __init__(self, adapters: Iterable[Adapter] = ()) -> None:
        self._adapters: dict[str, Adapter] = {}
        for adapter in adapters:
            self.register(adapter)

    def register(self, adapter: Adapter) -> None:
        provider = adapter.id
        if not provider:
            raise ValueError("Adapter id must be non-empty")
        if provider in self._adapters:
            raise ValueError("Adapter id must be unique")
        self._adapters[provider] = adapter

    def ids(self) -> tuple[str, ...]:
        return tuple(self._adapters)

    def get(self, provider: str) -> Adapter:
        adapter = self._adapters.get(provider)
        if adapter is None:
            # No implicit fallback: an unknown provider is an error, not a substitution.
            raise ProviderDisabledError(f"{provider} is not registered; no fallback was attempted")
        return adapter

    def run(self, provider: str, context: StepContext) -> StepResult:
        return self.get(provider).run(context)

    def status(self) -> dict[str, object]:
        return {provider: {"registered": True} for provider in self._adapters}
