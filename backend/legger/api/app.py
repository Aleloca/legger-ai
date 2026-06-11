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
by every request):

- ``app.state.settings``: the resolved Settings.
- ``app.state.engine``: pooled SQLAlchemy engine (lazy: no connection is
  opened until the first query, so app startup never blocks on Postgres).
- ``app.state.qdrant``: QdrantClient with the search-path timeout
  (:data:`~legger.retrieval.search.SEARCH_CLIENT_TIMEOUT_S`, 15 s).
- ``app.state.anthropic``: shared Anthropic client (F2 /chat: query
  understanding + generation).
- ``app.state.embedder``: the query embedder
  (``Settings.embedder_name`` — must match the indexed collection).
  Construction is API-light (no model download — the Voyage client is
  lazy), but it fails fast on a missing VOYAGE_API_KEY; that failure is
  caught and logged so the app still starts and serves /acts — /chat then
  answers with an SSE ``error`` event (``app.state.embedder is None``).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from anthropic import Anthropic
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from qdrant_client import QdrantClient

from legger.api.acts import router as acts_router
from legger.api.chat import router as chat_router
from legger.api.search import router as search_router
from legger.db import get_engine
from legger.retrieval.embedders import get_embedder
from legger.retrieval.search import SEARCH_CLIENT_TIMEOUT_S
from legger.settings import Settings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the legger API app around *settings* (default: env-resolved)."""
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        app.state.engine = get_engine(settings)
        app.state.qdrant = QdrantClient(url=settings.qdrant_url, timeout=SEARCH_CLIENT_TIMEOUT_S)
        if not settings.anthropic_api_key:
            # Symmetric with the embedder degrade below: the app still
            # starts and serves /acts, but /chat calls will fail at the
            # Anthropic API and surface SSE error events.
            logger.warning("ANTHROPIC_API_KEY is empty; /chat will return error events")
        app.state.anthropic = Anthropic(api_key=settings.anthropic_api_key)
        try:
            app.state.embedder = get_embedder(settings.embedder_name)
        except Exception:
            # Typically a missing VOYAGE_API_KEY: keep serving /acts, let
            # /chat degrade to its error event (see legger.api.chat).
            logger.warning(
                "embedder %r unavailable; /chat will return error events and "
                "/search loses its semantic tier",
                settings.embedder_name,
                exc_info=True,
            )
            app.state.embedder = None
        try:
            yield
        finally:
            # Nested so every client is released even if an earlier close raises.
            try:
                app.state.anthropic.close()
            finally:
                try:
                    app.state.qdrant.close()
                finally:
                    app.state.engine.dispose()

    app = FastAPI(title="legger.ai API", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        # Settings-driven (CORS_ORIGINS, comma-separated): defaults to the
        # Next.js dev server; production sets the real site origin (H1).
        allow_origins=settings.cors_origin_list(),
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        """Liveness probe: no dependencies, no I/O (Docker/proxy healthchecks)."""
        return {"status": "ok"}

    app.include_router(acts_router)
    app.include_router(chat_router)
    app.include_router(search_router)
    return app


app = create_app()
