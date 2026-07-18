from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from ..config import Settings


@dataclass(frozen=True, slots=True)
class OpenAIConfig:
    base_url: str
    model: str
    api_key: str | None

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.api_key.strip())


def _config_path(settings: Settings) -> Path:
    return settings.workspace_dir / "secrets" / "openai.json"


def read_openai_config(settings: Settings) -> OpenAIConfig:
    base_url = settings.openai_base_url.rstrip("/")
    model = settings.openai_model.strip()
    api_key = settings.openai_api_key
    path = _config_path(settings)
    if path.is_file():
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise RuntimeError("PROVIDER_CONFIG_FILE_INVALID") from exc
        if isinstance(stored, dict):
            base_url = str(stored.get("base_url") or base_url).rstrip("/")
            model = str(stored.get("model") or model).strip()
            api_key = str(stored.get("api_key") or api_key or "").strip() or None
    return OpenAIConfig(base_url=base_url, model=model, api_key=api_key)


def write_openai_config(
    settings: Settings,
    *,
    api_key: str | None,
    base_url: str | None,
    model: str | None,
) -> OpenAIConfig:
    current = read_openai_config(settings)
    next_key = api_key.strip() if api_key and api_key.strip() else current.api_key
    next_url = (base_url or current.base_url).strip().rstrip("/")
    next_model = (model or current.model).strip()
    if not next_key:
        raise ValueError("OPENAI_API_KEY_REQUIRED")
    if not next_url.startswith(("https://", "http://127.0.0.1", "http://localhost")):
        raise ValueError("OPENAI_BASE_URL_INVALID")
    if not next_model:
        raise ValueError("OPENAI_MODEL_REQUIRED")

    path = _config_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(f".tmp-{uuid4().hex}")
    temp.write_text(
        json.dumps(
            {"api_key": next_key, "base_url": next_url, "model": next_model},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    os.replace(temp, path)
    return OpenAIConfig(next_url, next_model, next_key)
