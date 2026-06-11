"""FastAPI application factory (Task F1).

Run the dev server from ``backend/`` with::

    uv run uvicorn legger.api.app:app --reload --port 8000

(no ``legger api`` CLI subcommand — uvicorn's own CLI already does
everything a wrapper would: reload, workers, bind address).

:func:`create_app` is a factory so tests can build isolated apps with their
own :class:`~legger.settings.Settings` (fixture corpus path, throwaway DB
URL) instead of mutating a module global. The module-level ``app`` is the
production instance uvicorn imports.

Shared clients live on ``app.state`` (created once in the lifespan, reused
by every request — F2's /chat will pick them up from there too):

- ``app.state.settings``: the resolved Settings.
- ``app.state.engine``: pooled SQLAlchemy engine (lazy: no connection is
  opened until the first query, so app startup never blocks on Postgres).
- ``app.state.qdrant``: QdrantClient with the search-path timeout
  (:data:`~legger.retrieval.search.SEARCH_CLIENT_TIMEOUT_S`, 15 s) — unused
  by F1 itself, created here so F2/F4 inherit a single shared client.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from qdrant_client import QdrantClient

from legger.api.acts import router as acts_router
from legger.db import get_engine
from legger.retrieval.search import SEARCH_CLIENT_TIMEOUT_S
from legger.settings import Settings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

#: Next.js dev server origin (G1). Production origins are an H1/H2 concern.
CORS_ORIGINS = ["http://localhost:3000"]


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the legger API app around *settings* (default: env-resolved)."""
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        app.state.engine = get_engine(settings)
        app.state.qdrant = QdrantClient(
            url=settings.qdrant_url, timeout=SEARCH_CLIENT_TIMEOUT_S
        )
        try:
            yield
        finally:
            # Nested so the engine is disposed even if the qdrant close raises.
            try:
                app.state.qdrant.close()
            finally:
                app.state.engine.dispose()

    app = FastAPI(title="legger.ai API", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(acts_router)
    return app


app = create_app()
