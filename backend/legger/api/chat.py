"""POST /chat: the grounded chat over SSE (Task F2).

THE SSE CONTRACT (the G2 frontend reads this)
---------------------------------------------

The response is ``text/event-stream``; events arrive in this order:

``event: status`` — data ``{"stage": "searching"}``
    Emitted immediately, before retrieval starts.

``event: sources`` — data ``{"sources": [{act_ref, article, title,
vigenza, anchor}]}``
    After retrieval: every provision consulted, deduplicated, in order of
    appearance. ``anchor`` is the split-view deep-link fragment for the
    article (e.g. ``art-2051``), derived from the retrieved chunk ids via
    :func:`legger.api.acts.anchor_from_chunk_segment` so it matches the
    anchors GET /acts/{act_ref} emits.

``event: token`` — data ``{"text": "..."}``
    Answer text deltas. Markers stream through as token text too: a
    complete ``[[...]]`` marker arrives as the ``text`` of its own token
    event (never split across token events), so the frontend can render the
    raw transcript by concatenating all tokens, or swap markers for chips
    as the matching citation events arrive.

``event: citation`` — data ``{marker, act_ref, article, comma, title,
vigenza, verified, reason}``
    Emitted right after the token event carrying a contract-format marker,
    with the verdict of the citation guardrail
    (:func:`legger.chat.guardrail.check_citation`). ``verified`` is false
    only when the cited act or article was NOT in the retrieval context
    (``reason`` is ``act_not_in_context`` / ``article_not_in_context``) —
    the UI should flag those. ``reason`` is ``comma_not_in_context`` when
    the act+article matched but the marker's comma could not be confirmed
    in the hits' commi lists: this is advisory (``verified`` stays true,
    the lists are structurally incomplete) and the UI may render a softer
    hint. Otherwise ``reason`` is ``ok``. ``title``/``vigenza`` come from
    the matching hit, or are ``null`` when ``verified`` is false. A
    bracket pair that does not parse as a marker gets no citation event
    (it flows through as plain token text).

``event: done`` — data ``{"stop_reason": str|null, "truncated": bool,
"config": {answer_model, answer_effort, qu_model, qu_effort}}``
    End of a successful answer. ``stop_reason`` is the model's stop reason
    (e.g. ``"end_turn"``), or ``null`` when the generation stream ended
    without reporting one. ``truncated`` is true when the model hit the
    token cap (``stop_reason == "max_tokens"``): the answer ends
    mid-sentence and the UI should say so. ``config`` is the EFFECTIVE
    beta-testing configuration the turn ran with (defaults filled in;
    efforts are ``null`` when omitted or unsupported by the chosen model) —
    transparency for testers comparing parameters.

BETA-TESTING CONFIG (per-conversation model/effort overrides)
-------------------------------------------------------------

The request body optionally carries ``config`` (see :class:`ChatConfig`):
which model generates the answer, which one does query understanding, and
the ``output_config.effort`` for each. Values are validated against the
single source of truth, :mod:`legger.chat.models_catalog` (422 on anything
outside the allowlists — client strings never reach the Anthropic API
unvalidated). ``GET /chat/models`` returns that catalog so the frontend
renders its selects from the backend, with no duplicated list.

``event: error`` — data ``{"message": "..."}``
    Terminal: emitted instead of further events when retrieval or
    generation fails. The message is user-safe Italian (no internals — the
    exception is logged server-side). No ``done`` event follows.

DEPLOYMENT NOTE (H1/H2) — no in-band heartbeats
-----------------------------------------------

This endpoint emits NO heartbeats: the generator is a sync pipeline that
is fully blocked while retrieval and the model's first token are in
flight, so nothing can be interleaved during exactly the windows where a
keep-alive would matter. The longest silent stretches are retrieval plus
time-to-first-token (tens of seconds under load).

Any reverse proxy in front of ``/chat`` MUST therefore allow at least 60s
of response idle time, e.g. nginx ``proxy_read_timeout 60s;`` (and
``proxy_buffering off;``, which the ``X-Accel-Buffering: no`` header also
requests), or the equivalent for other proxies. Otherwise the proxy will
cut the stream mid-answer during a silent window.

Design notes
------------

The route is a sync ``def``: Starlette runs it (and iterates the sync
generator) in its threadpool, so the blocking pipeline —
:func:`~legger.retrieval.pipeline.retrieve` and the Anthropic stream —
never sits on the event loop. Clients come from ``app.state`` (created
once in the lifespan, see :mod:`legger.api.app`).
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Literal

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator, model_validator

from legger.api.acts import anchor_from_chunk_segment
from legger.chat.generate import MODEL_SONNET, stream_answer
from legger.chat.guardrail import check_citation
from legger.chat.models_catalog import (
    ALLOWED_ANSWER_MODELS,
    ALLOWED_QU_MODELS,
    EFFORT_LEVELS,
    catalog_payload,
    effective_effort,
)
from legger.chat.stream import MarkerParser, MarkerPiece, Piece, parse_marker
from legger.chat.understanding import MODEL_HAIKU
from legger.retrieval.pipeline import retrieve

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastapi import FastAPI

    from legger.chat.types import Message
    from legger.retrieval.pipeline import RetrievalResult
    from legger.retrieval.search import SearchHit

logger = logging.getLogger(__name__)

router = APIRouter()

#: Request-body bounds (validated by pydantic -> 422).
MAX_TURNS = 20
MAX_CONTENT_CHARS = 8000

#: User-safe error message (Italian, no internals).
ERROR_MESSAGE = (
    "Si è verificato un errore durante l'elaborazione della richiesta. Riprova tra qualche istante."
)


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=MAX_CONTENT_CHARS)


class ChatConfig(BaseModel):
    """Per-conversation model/effort overrides (beta-testing phase).

    All fields optional: ``None`` keeps today's defaults. Values are
    validated against :mod:`legger.chat.models_catalog` — the single source
    of truth — so an unknown model or effort is a 422 and NEVER reaches the
    Anthropic API.
    """

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


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=MAX_TURNS)
    config: ChatConfig | None = None

    @model_validator(mode="after")
    def _last_turn_is_user(self) -> ChatRequest:
        if self.messages[-1].role != "user":
            raise ValueError("l'ultimo messaggio deve essere dell'utente")
        return self


def _sse(event: str, data: dict) -> str:
    """One SSE event frame."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _sources_payload(result: RetrievalResult) -> list[dict]:
    """The ``sources`` event payload, with split-view anchors.

    The anchor comes from the article segment of the first hit's chunk id
    (``{act_ref}#art-{number}[.{occurrence}]#{i}``) so repeated article
    numbers keep their ``.{occurrence}`` disambiguator; a malformed chunk id
    falls back to the plain article number.
    """
    anchors: dict[tuple[str, str], str] = {}
    for hit in result.hits:
        parts = hit.chunk_id.split("#")
        segment = parts[1] if len(parts) >= 2 and parts[1] else f"art-{hit.article}"
        anchors.setdefault((hit.act_ref, hit.article), anchor_from_chunk_segment(segment))
    return [
        {
            "act_ref": source.act_ref,
            "article": source.article,
            "title": source.title,
            "vigenza": source.vigenza,
            "anchor": anchors.get(
                (source.act_ref, source.article),
                anchor_from_chunk_segment(f"art-{source.article}"),
            ),
        }
        for source in result.sources
    ]


def _piece_events(piece: Piece, hits: list[SearchHit]) -> Iterator[str]:
    """token (+ citation) events for one parsed stream piece."""
    if isinstance(piece, MarkerPiece):
        yield _sse("token", {"text": piece.raw})
        parsed = parse_marker(piece.raw)
        if parsed is None:
            return  # not contract format: plain text, no citation event
        check = check_citation(parsed, hits)
        yield _sse(
            "citation",
            {
                "marker": piece.raw,
                "act_ref": parsed.act_ref,
                "article": parsed.article,
                "comma": parsed.comma,
                "title": check.hit.act_title if check.hit is not None else None,
                "vigenza": check.hit.vigenza if check.hit is not None else None,
                "verified": check.verified,
                "reason": check.reason,
            },
        )
    elif piece.text:
        yield _sse("token", {"text": piece.text})


def _effective_config(config: ChatConfig | None) -> dict[str, str | None]:
    """The configuration the turn actually runs with (defaults filled in).

    Efforts are ``None`` when omitted or when the chosen model does not
    support ``output_config.effort`` (build_model_kwargs drops it) — what is
    reported here is exactly what reaches the Anthropic API.
    """
    config = config or ChatConfig()
    answer_model = config.answer_model or MODEL_SONNET
    qu_model = config.qu_model or MODEL_HAIKU
    return {
        "answer_model": answer_model,
        "answer_effort": effective_effort(answer_model, config.answer_effort),
        "qu_model": qu_model,
        "qu_effort": effective_effort(qu_model, config.qu_effort),
    }


def _event_stream(
    messages: list[Message], app: FastAPI, config: ChatConfig | None = None
) -> Iterator[str]:
    """The SSE event generator (see the module docstring for the contract)."""
    state = app.state
    config = config or ChatConfig()
    yield _sse("status", {"stage": "searching"})

    if getattr(state, "embedder", None) is None:
        # The lifespan could not build the embedder (see legger.api.app).
        logger.error("/chat unavailable: no embedder on app.state")
        yield _sse("error", {"message": ERROR_MESSAGE})
        return

    try:
        result = retrieve(
            messages,
            qdrant_client=state.qdrant,
            engine=state.engine,
            anthropic_client=state.anthropic,
            collection=state.settings.qdrant_collection,
            embedder=state.embedder,
            qu_model=config.qu_model,
            qu_effort=config.qu_effort,
        )
    except Exception:
        logger.exception("/chat retrieval failed")
        yield _sse("error", {"message": ERROR_MESSAGE})
        return

    parser = MarkerParser()
    stop_reason: str | None = None
    generation = stream_answer(
        messages,
        result.hits,
        anthropic_client=state.anthropic,
        model=config.answer_model,
        effort=config.answer_effort,
    )
    try:
        yield _sse("sources", {"sources": _sources_payload(result)})
        while True:
            try:
                delta = next(generation)
            except StopIteration as stop:
                stop_reason = stop.value  # stream_answer's return value
                break
            for piece in parser.feed(delta):
                yield from _piece_events(piece, result.hits)
        for piece in parser.flush():
            yield from _piece_events(piece, result.hits)
    except Exception:
        logger.exception("/chat generation failed mid-stream")
        yield _sse("error", {"message": ERROR_MESSAGE})
        return
    finally:
        # Deterministic teardown: close() runs stream_answer's cleanup (the
        # Anthropic stream context manager) right now, both on normal exit
        # and when the client disconnects (GeneratorExit at a yield above),
        # instead of whenever the abandoned generator is garbage-collected.
        generation.close()

    yield _sse(
        "done",
        {
            "stop_reason": stop_reason,
            "truncated": stop_reason == "max_tokens",
            "config": _effective_config(config),
        },
    )


@router.get("/chat/models")
def chat_models() -> dict:
    """The model/effort catalog for the beta-testing settings panel.

    The frontend renders its selects FROM this payload (ids, labels, prices
    per Mtok, effort support, defaults) — the allowlist lives only in
    :mod:`legger.chat.models_catalog`.
    """
    return catalog_payload()


@router.post("/chat")
def chat(payload: ChatRequest, request: Request) -> StreamingResponse:
    """Stream the grounded answer for a conversation as SSE."""
    messages: list[Message] = [{"role": m.role, "content": m.content} for m in payload.messages]
    return StreamingResponse(
        _event_stream(messages, request.app, payload.config),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Tell nginx-style proxies not to buffer the stream.
            "X-Accel-Buffering": "no",
        },
    )
