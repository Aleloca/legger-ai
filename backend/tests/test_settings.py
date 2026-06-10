"""Tests for legger.settings."""

from pathlib import Path

import pytest

from legger.settings import Settings


def test_settings_from_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@db:5432/x")
    monkeypatch.setenv("QDRANT_URL", "http://qdrant:6333")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("VOYAGE_API_KEY", "pa-test")
    monkeypatch.setenv("CORPUS_PATH", "/tmp/corpus")

    settings = Settings()

    assert settings.database_url == "postgresql+psycopg://u:p@db:5432/x"
    assert settings.qdrant_url == "http://qdrant:6333"
    assert settings.anthropic_api_key == "sk-ant-test"
    assert settings.voyage_api_key == "pa-test"
    assert settings.corpus_path == Path("/tmp/corpus")


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("DATABASE_URL", "QDRANT_URL", "ANTHROPIC_API_KEY", "VOYAGE_API_KEY", "CORPUS_PATH"):
        monkeypatch.delenv(key, raising=False)

    settings = Settings(_env_file=None)

    assert settings.database_url == "postgresql+psycopg://legger:legger@localhost:5432/legger"
    assert settings.qdrant_url == "http://localhost:6333"
    assert settings.anthropic_api_key == ""
    assert settings.voyage_api_key == ""
    assert settings.corpus_path == Path("../italia-corpus")
