"""POST /feedback: 👍/👎 on an assistant answer, with config correlation.

The frontend sends one row per completed assistant message: the rating, the
optional reason (👎), the question/answer pair, the message's citation list
and the EFFECTIVE per-turn config (the ``config`` block of the ``done`` SSE
event — see :mod:`legger.api.chat`). Stored in ``message_feedback``
(:mod:`legger.db`) so `legger feedback report` can break the 👍-rate down by
model/effort combination.

Validation notes
----------------

- ``citations`` keeps only the lean shape (marker/act_ref/article/comma/
  verified): extra keys the frontend may carry (title, vigenza, reason) are
  dropped, not rejected — the stored JSONB stays stable across UI changes.
- ``config`` is the opposite: ``extra="forbid"`` plus the
  :mod:`legger.chat.models_catalog` allowlists, because this field feeds the
  per-config breakdown — garbage keys or invented model ids would silently
  pollute the report, so they are a 422 instead.
- No auth and no user identifier: the row is anonymous by design (beta
  phase); volume is bounded by the UI (one feedback per message).

Errors: 422 on validation (FastAPI default), 503 with a user-safe Italian
message when Postgres is unreachable (the exception is logged server-side).
"""

from __future__ import annotations

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.exc import SQLAlchemyError

from legger.chat.models_catalog import ALLOWED_ANSWER_MODELS, ALLOWED_QU_MODELS, EFFORT_LEVELS
from legger.db import insert_feedback

logger = logging.getLogger(__name__)

router = APIRouter()

#: Request-body bounds (validated by pydantic -> 422).
MAX_REASON_CHARS = 2000
MAX_QUESTION_CHARS = 8000  # = chat.MAX_CONTENT_CHARS: the question IS a chat turn
MAX_ANSWER_CHARS = 32000
MAX_CITATIONS = 100

#: User-safe error message when the insert cannot reach Postgres.
DB_ERROR_MESSAGE = "Impossibile salvare il feedback in questo momento. Riprova più tardi."


class FeedbackCitation(BaseModel):
    """One citation of the rated answer, lean shape (extra keys dropped)."""

    marker: str = Field(max_length=500)
    act_ref: str = Field(max_length=200)
    article: str = Field(max_length=50)
    comma: str | None = Field(default=None, max_length=50)
    verified: bool


class FeedbackConfig(BaseModel):
    """The EFFECTIVE config of the rated turn (the ``done`` event's block).

    ``extra="forbid"`` + the models_catalog allowlists: this field feeds the
    per-config breakdown of `legger feedback report`, so unknown keys or
    invented model ids are rejected (422) instead of stored.
    """

    model_config = ConfigDict(extra="forbid")

    answer_model: str | None = None
    answer_effort: str | None = None
    qu_model: str | None = None
    qu_effort: str | None = None

    @field_validator("answer_model")
    @classmethod
    def _answer_model_allowed(cls, v: str | None) -> str | None:
        if v is not None and v not in ALLOWED_ANSWER_MODELS:
            raise ValueError(f"modello non ammesso per le risposte: {v!r}")
        return v

    @field_validator("qu_model")
    @classmethod
    def _qu_model_allowed(cls, v: str | None) -> str | None:
        if v is not None and v not in ALLOWED_QU_MODELS:
            raise ValueError(f"modello non ammesso per la comprensione della domanda: {v!r}")
        return v

    @field_validator("answer_effort", "qu_effort")
    @classmethod
    def _effort_allowed(cls, v: str | None) -> str | None:
        if v is not None and v not in EFFORT_LEVELS:
            raise ValueError(f"livello di effort non ammesso: {v!r}")
        return v


class FeedbackRequest(BaseModel):
    rating: Literal[-1, 1]
    reason: str | None = Field(default=None, max_length=MAX_REASON_CHARS)
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    answer: str = Field(min_length=1, max_length=MAX_ANSWER_CHARS)
    citations: Annotated[list[FeedbackCitation], Field(max_length=MAX_CITATIONS)] = []
    config: FeedbackConfig = FeedbackConfig()


@router.post("/feedback", status_code=204)
def post_feedback(payload: FeedbackRequest, request: Request) -> Response:
    """Store one feedback row; 204 on success (the UI shows its own thanks)."""
    try:
        insert_feedback(
            request.app.state.engine,
            rating=payload.rating,
            reason=payload.reason,
            question=payload.question,
            answer=payload.answer,
            citations=[c.model_dump() for c in payload.citations],
            config=payload.config.model_dump(),
        )
    except SQLAlchemyError:
        logger.exception("/feedback insert failed")
        raise HTTPException(status_code=503, detail=DB_ERROR_MESSAGE) from None
    return Response(status_code=204)
