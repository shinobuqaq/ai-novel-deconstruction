from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import router
from .config import Settings, get_settings
from .db import create_db_engine, create_session_factory
from .models import Base
from .providers import ProviderRegistry, create_default_provider_registry


def create_app(
    settings: Settings | None = None,
    provider_registry: ProviderRegistry | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    settings.ensure_directories()

    engine = create_db_engine(settings)
    session_factory = create_session_factory(engine)
    if settings.auto_create_schema:
        Base.metadata.create_all(engine)

    app = FastAPI(title=settings.app_name, version="0.0.1")
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.provider_registry = (
        provider_registry or create_default_provider_registry(settings)
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"] ,
        allow_headers=["*"] ,
    )
    app.include_router(router)
    return app


app = create_app()
