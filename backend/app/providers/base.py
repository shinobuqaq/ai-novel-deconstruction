from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    raw_text: str
    parsed: dict[str, Any]
    prompt_tokens: int
    completion_tokens: int


class Provider(Protocol):
    async def complete(self, *, task_kind: str, payload: dict[str, Any]) -> ProviderResponse:
        ...
