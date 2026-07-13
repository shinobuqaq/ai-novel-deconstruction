from __future__ import annotations

import json
from typing import Any

from .base import ProviderResponse


class FakeProvider:
    """Deterministic provider used by tests and M0 development."""

    async def complete(self, *, task_kind: str, payload: dict[str, Any]) -> ProviderResponse:
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
