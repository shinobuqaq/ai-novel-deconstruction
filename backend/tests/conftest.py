from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    settings = Settings(
        database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        workspace_dir=tmp_path / "workspace",
        auto_create_schema=True,
    )
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client
