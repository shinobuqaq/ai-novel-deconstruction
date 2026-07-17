from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    raw_text: str
    parsed: dict[str, Any]
    prompt_tokens: int
    completion_tokens: int


class ProviderError(Exception):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        retryable: bool,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds


@runtime_checkable
class Provider(Protocol):
    name: str

    async def complete(self, *, task_kind: str, payload: dict[str, Any]) -> ProviderResponse:
        ...
