"""Tests for app-level concerns (Task H1): /healthz and settings-driven CORS.

Same lifespan seam as the other API tests: ``legger.api.app.get_embedder``
is stubbed so building the app never needs a Voyage key, and no test below
touches Postgres/Qdrant (the lifespan clients are lazy).
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from legger.api import app as app_mod
from legger.api.app import create_app
from legger.settings import Settings


@pytest.fixture(autouse=True)
def stub_embedder(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(app_mod, "get_embedder", lambda name: object())
    yield


def make_client(settings: Settings | None = None) -> TestClient:
    return TestClient(create_app(settings or Settings(anthropic_api_key="test-key")))


def test_healthz_ok() -> None:
    with make_client() as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_cors_default_allows_localhost_dev_origin() -> None:
    with make_client() as client:
        response = client.get("/healthz", headers={"Origin": "http://localhost:3000"})

    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_cors_settings_driven_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(
        anthropic_api_key="test-key",
        cors_origins="https://legger.ai, https://www.legger.ai",
    )
    with make_client(settings) as client:
        allowed = client.get("/healthz", headers={"Origin": "https://www.legger.ai"})
        denied = client.get("/healthz", headers={"Origin": "http://localhost:3000"})

    assert allowed.headers["access-control-allow-origin"] == "https://www.legger.ai"
    assert "access-control-allow-origin" not in denied.headers


def test_cors_origin_list_parses_comma_separated() -> None:
    settings = Settings(cors_origins=" https://a.example ,, https://b.example ")

    assert settings.cors_origin_list() == ["https://a.example", "https://b.example"]


async def test_rate_limiter_absent_when_disabled():
    app = create_app(Settings(_env_file=None, rate_limit_enabled=False))
    async with app.router.lifespan_context(app):
        assert app.state.rate_limiter is None


async def test_rate_limiter_present_when_enabled(monkeypatch):
    import fakeredis

    import legger.api.ratelimit as rl_mod
    monkeypatch.setattr(
        rl_mod, "build_redis", lambda url: fakeredis.FakeStrictRedis(decode_responses=True)
    )
    app = create_app(Settings(_env_file=None, rate_limit_enabled=True))
    async with app.router.lifespan_context(app):
        assert app.state.rate_limiter is not None
