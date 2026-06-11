"""Unified retrieval pipeline (Task E5).

One entry point — :func:`retrieve` — composes every retrieval stage built in
Fase E on top of the C4 hybrid core, in this order:

1. **Query understanding** (E2, :func:`~legger.chat.understanding
   .understand_query`): rewrites the current user message into a standalone
   query and flags historical intent. Contractually it never raises (verbatim
   fallback built in); the pipeline still guards it defensively — if it DOES
   raise, the original last user message is used and ``query_analysis`` comes
   back ``None``.
2. **Reference extraction** (E1, :func:`~legger.retrieval.fastpath
   .extract_refs`): on the ORIGINAL last user message first — the rewrite is
   model output and may garble estremi the user typed exactly — and only when
   that yields nothing, on the rewritten query (which is where resolved
   anaphora like "e l'articolo successivo?" become extractable).
3. **Context binding for unbound refs** (``act_ref=None``): an article cited
   without a source ("cosa dice l'articolo 275?") is bound KEEPING IT SIMPLE:
   if the OTHER extracted refs name exactly one act, bind to it; otherwise,
   if the previous assistant turn's citation markers (``[[act_ref|...]]``,
   regex ``\\[\\[([a-z0-9-]+)\\|``) name exactly one act, bind to that; any
   other case (no context, or 2+ candidate acts) DROPS the ref — a wrong
   bind sends garbage chunks to the LLM, a drop just falls back to hybrid.
4. **Fast path** (E1, :func:`~legger.retrieval.fastpath.resolve_refs`, with
   ``engine`` for the acts-table probe): ARTICLE-level refs resolve to
   deterministic hits that go FIRST in the result — an exact citation beats
   any similarity score. ACT-level refs (no article) are SUPPLEMENTS, not
   replacements: at most :data:`ACT_SUPPLEMENT_CAP` (2) of their chunks are
   appended AFTER the hybrid hits, because "cosa prevede il d.lgs. 81/2008?"
   is still best answered by hybrid search over its full text.
5. **Hybrid search** (C4) with the REWRITTEN query. ``vigenza="vigente"`` by
   default; ``wants_historical=True`` disables the filter entirely (``None``)
   — actual temporal version fetching via git is Fase 3, NOT here; until
   then the honest behavior is simply not to hide non-vigente chunks. When
   ``Settings.rerank_enabled``: fetch :data:`RERANK_CANDIDATES` (50) and let
   the E3 cross-encoder cut back to ``k``; a rerank failure degrades to the
   RRF top-``k``. A hybrid failure PROPAGATES — it is the core, there is
   nothing to degrade to.
6. **Merge**: fastpath article hits first, then hybrid, then act-level
   supplements (max 2); dedup by ``chunk_id`` (first occurrence wins, so a
   chunk found by both keeps its fastpath position); total capped at
   ``k +`` :data:`MERGE_OVERFLOW` (k+4) — the overflow leaves room for
   fastpath wins without doubling the context.
7. **Citation following** (E4, :func:`~legger.retrieval.citations
   .follow_citations`, ``token_budget=citation_budget``): the returned NEW
   hits are appended LAST, outside the k+4 cap (the token budget already
   bounds them).
8. **Sources**: every hit that reaches the result (merged + followed),
   deduplicated on ``(act_ref, article)`` in order of appearance — the UI
   "fonti consultate" list.

Failure policy (rule of the module): every stage EXCEPT hybrid search is an
enhancement — its failure is logged with a warning and the pipeline degrades
to what the previous stages produced. Hybrid failure propagates.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from pydantic import BaseModel

from legger.chat.generate import last_user_message
from legger.chat.understanding import QueryAnalysis, understand_query
from legger.retrieval.citations import follow_citations
from legger.retrieval.fastpath import ExtractedRef, extract_refs, resolve_refs
from legger.retrieval.rerank import rerank
from legger.retrieval.search import SearchHit, hybrid_search
from legger.settings import Settings

if TYPE_CHECKING:
    from anthropic import Anthropic
    from qdrant_client import QdrantClient
    from sqlalchemy import Engine

    from legger.retrieval.embedders import Embedder

logger = logging.getLogger(__name__)

#: Merged-list cap is ``k + MERGE_OVERFLOW``: fastpath hits may push the list
#: past ``k``, but never unboundedly.
MERGE_OVERFLOW = 4
#: Max act-level fastpath chunks appended after the hybrid hits (supplements,
#: per the E1 guidance: act-level hits must not displace hybrid results).
ACT_SUPPLEMENT_CAP = 2
#: Hybrid pool fed to the cross-encoder when reranking is enabled (E3).
RERANK_CANDIDATES = 50

#: act_ref half of a ``[[act_ref|art.N|c.M]]`` citation marker (the F3/G3
#: contract format, see legger.chat.prompts) in an assistant message.
_MARKER_ACT_RE = re.compile(r"\[\[([a-z0-9-]+)\|")


class SourceInfo(BaseModel):
    """One consulted provision, for the UI "fonti consultate" list."""

    act_ref: str
    article: str
    title: str
    vigenza: str


class RetrievalResult(BaseModel):
    """What :func:`retrieve` hands to generation (and to the API layer)."""

    hits: list[SearchHit]  # ordered: what goes to the LLM
    sources: list[SourceInfo]  # ALL consulted, dedup by (act_ref, article)
    used_fastpath: bool  # at least one fastpath hit made the final list
    query_analysis: QueryAnalysis | None  # transparency/debug; None = QU crashed


def _assistant_marker_acts(messages: list[dict]) -> list[str]:
    """Distinct act_refs cited by the LAST assistant turn, in marker order."""
    for message in reversed(messages):
        if message["role"] == "assistant":
            return list(dict.fromkeys(_MARKER_ACT_RE.findall(str(message["content"]))))
    return []


def _bind_unbound_refs(refs: list[ExtractedRef], messages: list[dict]) -> list[ExtractedRef]:
    """Bind ``act_ref=None`` article refs from context, or drop them.

    Candidate acts come from the other extracted refs first (the same message
    usually names the act once: "art. 14 del d.lgs. 81/2008 e l'articolo
    26"); when no other ref names an act, from the previous assistant turn's
    citation markers. Exactly one distinct candidate -> bind; zero or 2+ ->
    drop (documented choice: a wrong bind poisons the context, a drop merely
    falls back to hybrid search).
    """
    bound_acts = list(dict.fromkeys(r.act_ref for r in refs if r.act_ref is not None))
    candidates = bound_acts if bound_acts else _assistant_marker_acts(messages)
    out: list[ExtractedRef] = []
    for ref in refs:
        if ref.act_ref is not None:
            out.append(ref)
        elif ref.article is not None and len(candidates) == 1:
            out.append(ref.model_copy(update={"act_ref": candidates[0]}))
        else:
            logger.debug(
                "dropping unbound ref %s: %d candidate act(s) in context", ref, len(candidates)
            )
    return out


def retrieve(
    messages: list[dict],
    *,
    qdrant_client: QdrantClient,
    engine: Engine | None,
    anthropic_client: Anthropic,
    collection: str,
    embedder: Embedder,
    k: int = 10,
    citation_budget: int = 4000,
    rerank_enabled: bool | None = None,
) -> RetrievalResult:
    """Run the full retrieval pipeline for the current user turn.

    See the module docstring for the stage-by-stage logic and the failure
    policy. ``engine`` may be ``None`` (the fastpath acts-table probe is then
    skipped; resolution falls through to Qdrant alone). ``rerank_enabled``
    overrides the E3 cross-encoder toggle; ``None`` (the default) reads
    ``Settings().rerank_enabled`` per call.

    This is a blocking call (Qdrant REST, Postgres, Anthropic, embedding):
    in async contexts run it in a threadpool (e.g.
    ``fastapi.concurrency.run_in_threadpool``), never on the event loop.

    Raises whatever :func:`~legger.retrieval.search.hybrid_search` raises
    (the core stage), and :class:`ValueError` when ``messages`` contains no
    user turn (caller bug).
    """
    original_query = str(last_user_message(messages))

    # 1. Query understanding (never raises by contract; guarded anyway).
    analysis: QueryAnalysis | None
    try:
        analysis = understand_query(messages, anthropic_client=anthropic_client)
    except Exception:
        logger.warning(
            "understand_query raised despite its no-raise contract; using the verbatim message",
            exc_info=True,
        )
        analysis = None
    rewritten_query = original_query
    if analysis is not None and analysis.rewritten_query.strip():
        rewritten_query = analysis.rewritten_query

    # 2-4. Fast path: extract (original first, then rewritten), bind/drop
    # unbound refs, resolve. Any failure degrades to hybrid-only. The two
    # resolve_refs calls share one failure domain (a single try block): if
    # the article-level resolve succeeds but the act-level one raises, BOTH
    # are discarded. Deliberate — both calls hit the same Qdrant/Postgres
    # backends, so a failure in one means the other's results are suspect
    # too, and the degrade target (hybrid-only) is the same either way.
    article_hits: list[SearchHit] = []
    act_hits: list[SearchHit] = []
    try:
        refs = extract_refs(original_query) or extract_refs(rewritten_query)
        refs = _bind_unbound_refs(refs, messages)
        article_refs = [r for r in refs if r.article is not None]
        act_refs = [r for r in refs if r.article is None]
        if article_refs:
            article_hits = resolve_refs(
                article_refs, qdrant_client=qdrant_client, collection=collection, engine=engine
            )
        if act_refs:
            act_hits = resolve_refs(
                act_refs, qdrant_client=qdrant_client, collection=collection, engine=engine
            )[:ACT_SUPPLEMENT_CAP]
    except Exception:
        logger.warning("fast path failed; continuing with hybrid search only", exc_info=True)
        article_hits, act_hits = [], []

    # 5. Hybrid search (the core: failures propagate). Historical intent
    # disables the vigenza filter (no git time travel yet — Fase 3).
    vigenza = None if analysis is not None and analysis.wants_historical else "vigente"
    if rerank_enabled is None:
        rerank_enabled = Settings().rerank_enabled
    if rerank_enabled:
        candidates = hybrid_search(
            rewritten_query,
            collection=collection,
            embedder=embedder,
            client=qdrant_client,
            k=RERANK_CANDIDATES,
            vigenza=vigenza,
        )
        try:
            hybrid_hits = rerank(rewritten_query, candidates, top_k=k)
        except Exception:
            logger.warning("rerank failed; falling back to the RRF order", exc_info=True)
            hybrid_hits = candidates[:k]
    else:
        hybrid_hits = hybrid_search(
            rewritten_query,
            collection=collection,
            embedder=embedder,
            client=qdrant_client,
            k=k,
            vigenza=vigenza,
        )

    # 6. Merge: fastpath article hits FIRST (deterministic beats similarity),
    # hybrid next, act-level supplements last; dedup on chunk_id; cap k+4.
    merged: list[SearchHit] = []
    seen_chunks: set[str] = set()
    for hit in (*article_hits, *hybrid_hits, *act_hits):
        if hit.chunk_id in seen_chunks:
            continue
        seen_chunks.add(hit.chunk_id)
        merged.append(hit)
        if len(merged) >= k + MERGE_OVERFLOW:
            break
    fastpath_chunks = {h.chunk_id for h in (*article_hits, *act_hits)}
    used_fastpath = any(h.chunk_id in fastpath_chunks for h in merged)

    # 7. Citation following: new hits appended LAST (budget-bounded).
    followed: list[SearchHit] = []
    try:
        followed = follow_citations(
            merged,
            qdrant_client=qdrant_client,
            collection=collection,
            engine=engine,
            token_budget=citation_budget,
        )
    except Exception:
        logger.warning("citation following failed; answering without rinvii", exc_info=True)
    hits = merged + followed

    # 8. Sources: every consulted provision, dedup by (act_ref, article).
    sources: list[SourceInfo] = []
    seen_sources: set[tuple[str, str]] = set()
    for hit in hits:
        key = (hit.act_ref, hit.article)
        if key in seen_sources:
            continue
        seen_sources.add(key)
        sources.append(
            SourceInfo(
                act_ref=hit.act_ref,
                article=hit.article,
                title=hit.act_title,
                vigenza=hit.vigenza,
            )
        )

    return RetrievalResult(
        hits=hits,
        sources=sources,
        used_fastpath=used_fastpath,
        query_analysis=analysis,
    )
