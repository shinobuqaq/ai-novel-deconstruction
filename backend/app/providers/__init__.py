from __future__ import annotations

from ..config import Settings, get_settings
from .fake import FakeProvider
from .openai_responses import OpenAIResponsesProvider
from .registry import ProviderRegistry


def create_default_provider_registry(settings: Settings | None = None) -> ProviderRegistry:
    settings = settings or get_settings()
    return ProviderRegistry([FakeProvider(), OpenAIResponsesProvider(settings)])


__all__ = ["ProviderRegistry", "create_default_provider_registry"]
