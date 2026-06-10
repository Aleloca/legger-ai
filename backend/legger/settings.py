"""Application settings, read from environment variables and the repo-root .env file."""

from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_REPO_ROOT / ".env", extra="ignore")

    database_url: str = "postgresql+psycopg://legger:legger@localhost:5432/legger"
    qdrant_url: str = "http://localhost:6333"
    anthropic_api_key: str = ""
    voyage_api_key: str = ""
    corpus_path: Path = Path("../italia-corpus")

    @field_validator("corpus_path", mode="after")
    @classmethod
    def _resolve_corpus_path(cls, value: Path) -> Path:
        """Resolve relative corpus paths against the repo root, not the cwd.

        The default ``../italia-corpus`` therefore points at the corpus clone
        sitting next to this repo, regardless of where the process started.
        """
        if not value.is_absolute():
            value = (_REPO_ROOT / value).resolve()
        return value
