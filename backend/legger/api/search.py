"""GET /search: direct provision search, no LLM in the loop (Task F4).

Backs the frontend's standalone search box (G-phase): the user types either
an explicit reference ("art. 2051 c.c.") or free text ("responsabilità del
custode") and gets provisions back, each with a split-view ``anchor`` so a
click opens GET /acts/{act_ref} at the right article.

Two-tier flow, mirroring the E5 chat pipeline but without query
understanding (the raw ``q`` is the query):

1. **Exact tier** — :func:`~legger.retrieval.fastpath.extract_refs` over
   ``q``; article-level refs (act_ref AND article known) are resolved via
   :func:`~legger.retrieval.fastpath.resolve_refs` (Qdrant payload filter,
   Postgres probe for estremi slugs). Hits are marked ``"match": "exact"``
   and always come first. Act-level refs (no article) are deliberately NOT
   resolved here: for a *search* box the opening chunks of an act are a
   worse answer than the semantic tier ranking the act's relevant articles.
2. **Semantic tier** — when the exact tier returns nothing, or fewer than
   ``k`` results, :func:`~legger.retrieval.search.hybrid_search` (vigenza
   ``vigente``) fills the remainder, marked ``"match": "semantic"``,
   deduplicated against the exact tier by chunk id.

Degraded mode: when ``app.state.embedder`` is ``None`` (lifespan could not
build it, see :mod:`legger.api.app`) the semantic tier is unavailable — an
exact-tier-only response is still served when refs resolved, otherwise 502.

The route is a sync ``def`` (Starlette threadpool): both tiers block on
Qdrant/Voyage I/O and must not sit on the event loop. Failures of either
tier map to **502** with a user-safe Italian detail; internals are logged,
never leaked.
"""

from __future__ import annotations

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from legger.api.acts import anchor_from_chunk_segment
from legger.retrieval.fastpath import extract_refs, resolve_refs
from legger.retrieval.search import SearchHit, hybrid_search

logger = logging.getLogger(__name__)

router = APIRouter()

#: Query-string bounds (validated by FastAPI -> 422).
MAX_QUERY_CHARS = 500
MAX_K = 25

#: Snippet budget: ~240 chars of the chunk body (header excluded) is enough
#: for a result-list preview without shipping whole articles.
SNIPPET_CHARS = 240

#: User-safe error detail (Italian, no internals — mirrors /chat's policy).
ERROR_DETAIL = (
    "La ricerca non è al momento disponibile per un problema tecnico. Riprova tra qualche istante."
)


class SearchResult(BaseModel):
    act_ref: str
    article: str
    act_title: str
    snippet: str
    vigenza: str
    #: Split-view fragment for GET /acts/{act_ref} (e.g. ``art-2051``),
    #: derived from the chunk id so repeated article numbers keep their
    #: ``.occurrence`` disambiguator — same rule as /chat's sources event.
    anchor: str
    match: Literal["exact", "semantic"]


class SearchResponse(BaseModel):
    results: list[SearchResult]


def _snippet(hit: SearchHit) -> str:
    """First ~:data:`SNIPPET_CHARS` chars of the chunk body, header stripped.

    B5 chunk text is ``f"{header}\\n\\n{body}"`` (or just the header for
    body-less chunks), so the header is dropped by prefix match. Truncation
    backtracks to a word boundary and appends a single ellipsis.
    """
    body = hit.text
    if hit.header and body.startswith(hit.header):
        body = body[len(hit.header) :]
    body = body.strip()
    if len(body) <= SNIPPET_CHARS:
        return body
    window = body[: SNIPPET_CHARS + 1]  # +1: see whether char 240 ends a word
    head, _, _ = window.rpartition(" ")
    return (head.rstrip(" \n\t,;:") or window[:SNIPPET_CHARS]) + "…"


def _anchor(hit: SearchHit) -> str:
    """Split-view anchor from the chunk id's article segment (F3 rule).

    ``{act_ref}#art-{number}[.{occurrence}]#{i}`` -> ``art-number[-occ]``;
    a malformed chunk id falls back to the plain article number.
    """
    parts = hit.chunk_id.split("#")
    segment = parts[1] if len(parts) >= 2 and parts[1] else f"art-{hit.article}"
    return anchor_from_chunk_segment(segment)


def _result(hit: SearchHit, match: Literal["exact", "semantic"]) -> SearchResult:
    return SearchResult(
        act_ref=hit.act_ref,
        article=hit.article,
        act_title=hit.act_title,
        snippet=_snippet(hit),
        vigenza=hit.vigenza,
        anchor=_anchor(hit),
        match=match,
    )


@router.get("/search", response_model=SearchResponse)
def search(
    request: Request,
    q: Annotated[
        str,
        Query(
            min_length=1,
            max_length=MAX_QUERY_CHARS,
            description="Free text or an explicit reference (e.g. 'art. 2051 c.c.').",
        ),
    ],
    k: Annotated[int, Query(ge=1, le=MAX_K, description="Max results.")] = 10,
) -> SearchResponse:
    """Search provisions: exact reference matches first, semantic fill to *k*."""
    if not q.strip():
        # min_length=1 lets a whitespace-only q through; it is just as empty.
        raise HTTPException(status_code=422, detail="Il parametro 'q' non può essere vuoto.")
    state = request.app.state
    collection = state.settings.qdrant_collection

    results: list[SearchResult] = []
    seen: set[str] = set()

    refs = [r for r in extract_refs(q) if r.act_ref is not None and r.article is not None]
    if refs:
        try:
            exact_hits = resolve_refs(
                refs, qdrant_client=state.qdrant, collection=collection, engine=state.engine
            )
        except Exception:
            logger.exception("/search exact tier failed (q=%r)", q)
            raise HTTPException(status_code=502, detail=ERROR_DETAIL) from None
        for hit in exact_hits[:k]:
            seen.add(hit.chunk_id)
            results.append(_result(hit, "exact"))

    if len(results) < k:
        embedder = getattr(state, "embedder", None)
        if embedder is None:
            # Lifespan degrade (no Voyage key): exact-only is still a valid
            # answer; with nothing at all the endpoint is effectively down.
            logger.error("/search semantic tier unavailable: no embedder on app.state")
            if results:
                return SearchResponse(results=results)
            raise HTTPException(status_code=502, detail=ERROR_DETAIL)
        try:
            semantic_hits = hybrid_search(
                q, collection=collection, embedder=embedder, client=state.qdrant, k=k
            )
        except Exception:
            logger.exception("/search semantic tier failed (q=%r)", q)
            raise HTTPException(status_code=502, detail=ERROR_DETAIL) from None
        for hit in semantic_hits:
            if len(results) >= k:
                break
            if hit.chunk_id in seen:
                continue
            seen.add(hit.chunk_id)
            results.append(_result(hit, "semantic"))

    return SearchResponse(results=results)
