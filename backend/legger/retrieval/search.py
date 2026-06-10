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

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel
from qdrant_client import QdrantClient, models

from legger.retrieval.embedders import Embedder
from legger.retrieval.index import BM25_LANGUAGE, BM25_MODEL, DENSE_VECTOR, SPARSE_VECTOR

if TYPE_CHECKING:
    from fastembed import SparseTextEmbedding

_sparse_query_model: SparseTextEmbedding | None = None


def _get_sparse_query_model() -> SparseTextEmbedding:
    """Lazy singleton for the BM25 query embedder.

    Must be the exact fastembed model + language used at index time
    (:mod:`legger.retrieval.index`), or query terms tokenize/stem differently
    from the indexed documents and the sparse branch silently degrades.
    """
    global _sparse_query_model
    if _sparse_query_model is None:
        from fastembed import SparseTextEmbedding

        _sparse_query_model = SparseTextEmbedding(model_name=BM25_MODEL, language=BM25_LANGUAGE)
    return _sparse_query_model


def bm25_query_vector(query: str) -> models.SparseVector:
    """Embed a query for the ``bm25`` sparse branch (IDF is applied server-side)."""
    embedding = next(iter(_get_sparse_query_model().query_embed(query)))
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
    """Dense + BM25 hybrid search with RRF fusion; top-``k`` hits, best first."""
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
