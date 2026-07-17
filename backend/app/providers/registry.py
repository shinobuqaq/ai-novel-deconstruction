from __future__ import annotations

from collections.abc import Iterable

from .base import Provider, ProviderError


class ProviderRegistry:
    def __init__(self, providers: Iterable[Provider] = ()) -> None:
        self._providers: dict[str, Provider] = {}
        for provider in providers:
            self.register(provider)

    def register(self, provider: Provider) -> None:
        if not isinstance(provider, Provider):
            raise TypeError("INVALID_PROVIDER_CONTRACT")
        self._providers[provider.name] = provider

    def resolve(self, name: str) -> Provider:
        provider = self._providers.get(name)
        if provider is None:
            raise ProviderError(
                code="PROVIDER_NOT_CONFIGURED",
                message=f"Provider is not configured: {name}",
                retryable=False,
            )
        return provider
