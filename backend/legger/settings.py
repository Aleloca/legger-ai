"""Application settings, read from environment variables and the repo-root .env file."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_REPO_ROOT / ".env", extra="ignore")

    database_url: str = "postgresql+psycopg://legger:legger@localhost:5432/legger"
    qdrant_url: str = "http://localhost:6333"
    anthropic_api_key: str = ""
    voyage_api_key: str = ""
    corpus_path: Path = Path("../italia-corpus")
