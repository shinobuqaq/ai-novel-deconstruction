from __future__ import annotations

import json
from enum import StrEnum
from typing import Any

from .base import ProviderError, ProviderResponse


class FakeProviderMode(StrEnum):
    SUCCESS = "success"
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    TEMPORARY_UNAVAILABLE = "temporary_unavailable"
    INVALID_OUTPUT = "invalid_output"
    AUTH_FAILED = "auth_failed"
    BAD_REQUEST = "bad_request"
    PERMANENT_ERROR = "permanent_error"


class FakeProvider:
    """Deterministic provider used by tests and M0 development."""

    name = "fake"

    def __init__(
        self,
        *,
        mode: FakeProviderMode | str = FakeProviderMode.SUCCESS,
        retry_after_seconds: float | None = None,
    ) -> None:
        self.mode = FakeProviderMode(mode)
        self.retry_after_seconds = retry_after_seconds

    async def complete(self, *, task_kind: str, payload: dict[str, Any]) -> ProviderResponse:
        failures = {
            FakeProviderMode.TIMEOUT: (
                "PROVIDER_TIMEOUT",
                "Fake provider timed out.",
                True,
            ),
            FakeProviderMode.RATE_LIMIT: (
                "PROVIDER_RATE_LIMITED",
                "Fake provider rate limit reached.",
                True,
            ),
            FakeProviderMode.TEMPORARY_UNAVAILABLE: (
                "PROVIDER_UNAVAILABLE",
                "Fake provider is temporarily unavailable.",
                True,
            ),
            FakeProviderMode.INVALID_OUTPUT: (
                "PROVIDER_INVALID_OUTPUT",
                "Fake provider returned invalid JSON output.",
                True,
            ),
            FakeProviderMode.AUTH_FAILED: (
                "PROVIDER_AUTH_FAILED",
                "Fake provider authentication failed.",
                False,
            ),
            FakeProviderMode.BAD_REQUEST: (
                "PROVIDER_BAD_REQUEST",
                "Fake provider rejected the request.",
                False,
            ),
            FakeProviderMode.PERMANENT_ERROR: (
                "PROVIDER_PERMANENT_ERROR",
                "Fake provider returned a permanent error.",
                False,
            ),
        }
        failure = failures.get(self.mode)
        if failure is not None:
            code, message, retryable = failure
            raise ProviderError(
                code=code,
                message=message,
                retryable=retryable,
                retry_after_seconds=(
                    self.retry_after_seconds
                    if self.mode == FakeProviderMode.RATE_LIMIT
                    else None
                ),
            )

        parsed = {
            "provider": "fake",
            "task_kind": task_kind,
            "echo": payload,
        }
        raw_text = json.dumps(parsed, ensure_ascii=False, sort_keys=True)
        return ProviderResponse(
            raw_text=raw_text,
            parsed=parsed,
            prompt_tokens=max(1, len(raw_text) // 8),
            completion_tokens=max(1, len(raw_text) // 10),
        )
