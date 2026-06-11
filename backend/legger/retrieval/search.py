"""Hybrid (dense + BM25) retrieval over the Qdrant collections (Task C4).

One query goes through the Qdrant Query API with two ``prefetch`` branches —
the named dense vector ``dense`` (embedded with the collection's own
:class:`~legger.retrieval.embedders.Embedder`) and the named sparse vector
``bm25`` (fastembed ``Qdrant/bm25`` query embedding, same model/language as
index time) — fused with Reciprocal Rank Fusion server-side.

The ``vigenza`` filter is applied INSIDE both prefetch branches, not on the
fused result: filtering after fusion would let filtered-out points consume
prefetch slots and starve the final top-k. ``vigenza=None`` disables the
filter entirely (e.g. to search historical versions too).

This function is the production retrieval core (E5 builds on it): keep it a
pure query -> hits mapping, no query understanding, no reranking here.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel
from qdrant_client import QdrantClient, models

from legger.retrieval.embedders import Embedder
from legger.retrieval.index import DENSE_VECTOR, SPARSE_VECTOR, make_bm25_model

if TYPE_CHECKING:
    from fastembed import SparseTextEmbedding

#: REST timeout (seconds) for *search-path* Qdrant clients (eval, chat, API).
#: Deliberately short: a query that takes longer than this is an outage, not
#: a wait — only the indexing path gets the generous timeout
#: (``legger.retrieval.index.INDEXING_CLIENT_TIMEOUT_S``), so retrieval
#: latency can never silently grow to 120s.
SEARCH_CLIENT_TIMEOUT_S = 15

_sparse_query_model: SparseTextEmbedding | None = None
_sparse_query_model_lock = threading.Lock()


def _get_sparse_query_model() -> SparseTextEmbedding:
    """Lazy singleton for the BM25 query embedder (thread-safe).

    Must be the exact fastembed model + language used at index time
    (:func:`legger.retrieval.index.make_bm25_model`), or query terms
    tokenize/stem differently from the indexed documents and the sparse branch
    silently degrades. Double-checked locking: concurrent FastAPI requests
    must not race the (slow, stateful) model load.
    """
    global _sparse_query_model
    if _sparse_query_model is None:
        with _sparse_query_model_lock:
            if _sparse_query_model is None:
                _sparse_query_model = make_bm25_model()
    return _sparse_query_model


def bm25_query_vector(query: str) -> models.SparseVector:
    """Embed a query for the ``bm25`` sparse branch (IDF is applied server-side).

    Defensive on the fastembed side: if the model yields no embedding for the
    query (e.g. only stopwords/punctuation after tokenization), return an
    empty SparseVector — Qdrant accepts it and the sparse branch simply
    contributes nothing, so the search degrades gracefully to dense-only.
    """
    embedding = next(iter(_get_sparse_query_model().query_embed(query)), None)
    if embedding is None:
        return models.SparseVector(indices=[], values=[])
    return models.SparseVector(
        indices=embedding.indices.tolist(),
        values=embedding.values.tolist(),
    )


class SearchHit(BaseModel):
    """One retrieval result: the citation fields lifted out, full payload kept."""

    score: float
    chunk_id: str
    act_ref: str
    article: str
    act_title: str
    header: str
    text: str
    vigenza: str
    payload: dict[str, Any]


def _to_hit(point: models.ScoredPoint) -> SearchHit:
    payload = point.payload or {}
    return SearchHit(
        score=point.score,
        chunk_id=payload.get("chunk_id", ""),
        act_ref=payload.get("act_ref", ""),
        article=payload.get("article", ""),
        act_title=payload.get("act_title", ""),
        header=payload.get("header", ""),
        text=payload.get("text", ""),
        vigenza=payload.get("vigenza", ""),
        payload=payload,
    )


def hybrid_search(
    query: str,
    *,
    collection: str,
    embedder: Embedder,
    client: QdrantClient,
    k: int = 10,
    vigenza: str | None = "vigente",
    prefetch_k: int = 50,
) -> list[SearchHit]:
    """Dense + BM25 hybrid search with RRF fusion; top-``k`` hits, best first.

    ``k`` and ``prefetch_k`` are linked: the fused result can only draw from
    the two prefetch pools, so at most ~``2 * prefetch_k`` distinct points are
    available and asking for ``k`` beyond that returns no extra hits — keep
    ``k <= ~2 * prefetch_k`` (in practice well below, since the branches
    overlap).

    Raises :class:`ValueError` on an empty/whitespace-only query — it is
    meaningless for both the dense and the sparse branch.
    """
    if not query.strip():
        raise ValueError("hybrid_search: query must be a non-empty string")
    query_filter = None
    if vigenza is not None:
        query_filter = models.Filter(
            must=[models.FieldCondition(key="vigenza", match=models.MatchValue(value=vigenza))]
        )
    response = client.query_points(
        collection_name=collection,
        prefetch=[
            models.Prefetch(
                query=embedder.embed_query(query),
                using=DENSE_VECTOR,
                filter=query_filter,
                limit=prefetch_k,
            ),
            models.Prefetch(
                query=bm25_query_vector(query),
                using=SPARSE_VECTOR,
                filter=query_filter,
                limit=prefetch_k,
            ),
        ],
        query=models.FusionQuery(fusion=models.Fusion.RRF),
        limit=k,
        with_payload=True,
    )
    return [_to_hit(point) for point in response.points]
