from __future__ import annotations

from .fake import FakeProvider
from .registry import ProviderRegistry


def create_default_provider_registry() -> ProviderRegistry:
    return ProviderRegistry([FakeProvider()])


__all__ = ["ProviderRegistry", "create_default_provider_registry"]
