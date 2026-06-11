"""Application settings, read from environment variables and the repo-root .env file."""

from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_REPO_ROOT / ".env", extra="ignore")

    database_url: str = "postgresql+psycopg://legger:legger@localhost:5432/legger"
    qdrant_url: str = "http://localhost:6333"
    #: Qdrant collection served by the API (/chat retrieval). "norme" is the
    #: D2 bootstrap default; override via QDRANT_COLLECTION (e.g.
    #: "norme_voyage4large" for the completed bootstrap collection).
    qdrant_collection: str = "norme"
    #: Query embedder used by /chat retrieval. MUST match how the collection
    #: in ``qdrant_collection`` was indexed (same model => same vector space);
    #: override the pair together (EMBEDDER_NAME + QDRANT_COLLECTION).
    embedder_name: str = "voyage-4-large"
    anthropic_api_key: str = ""
    voyage_api_key: str = ""
    #: Ingestion alerting (Task D4): Telegram bot token + target chat id.
    #: Both empty by default — alerts are a logged no-op until configured
    #: (setup walkthrough in the legger/alerts.py module docstring).
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    corpus_path: Path = Path("../italia-corpus")
    #: Cross-encoder reranking in the retrieval pipeline (Task E3). Default per
    #: the plan's decision rule (recall@10 delta < 3pp on the measured eval —
    #: see backend/eval/results) — flip via RERANK_ENABLED if the trade-off
    #: changes. E5 consumes this.
    rerank_enabled: bool = False

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
