from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables or `.env`."""

    model_config = SettingsConfigDict(
        env_prefix="AND_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "AI Novel Deconstruction"
    host: str = "127.0.0.1"
    port: int = 8000
    database_url: str = "sqlite:///./workspace/app.db"
    workspace_dir: Path = Path("./workspace")
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            "http://127.0.0.1:5173",
            "http://localhost:5173",
        ]
    )
    auto_create_schema: bool = True
    worker_poll_seconds: float = 2.0
    worker_lease_seconds: int = 60
    provider_name: str = "fake"
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-5.6-terra"
    openai_api_key: str | None = None
    openai_timeout_seconds: float = 180.0
    openai_reasoning_effort: str = "auto"
    artifact_reconcile_seconds: float = 60.0
    artifact_recovery_stale_seconds: float = 300.0

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: object) -> object:
        if not isinstance(value, str):
            return value

        raw = value.strip()
        if not raw:
            return []

        if raw.startswith("["):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    "AND_CORS_ORIGINS must be a JSON string array or a comma-separated list"
                ) from exc
            if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
                raise ValueError("AND_CORS_ORIGINS JSON value must be a string array")
            return [item.strip() for item in parsed if item.strip()]

        return [item.strip() for item in raw.split(",") if item.strip()]

    def ensure_directories(self) -> None:
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        (self.workspace_dir / "artifacts").mkdir(parents=True, exist_ok=True)
        (self.workspace_dir / "sources").mkdir(parents=True, exist_ok=True)
        (self.workspace_dir / "secrets").mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
