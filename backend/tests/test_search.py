"""Unit tests for legger.retrieval.search (mocked Qdrant + embedder, no network).

Pins down the Query API request shape (two prefetch branches, RRF fusion,
vigenza filter inside both branches) and the point -> SearchHit mapping.
"""

from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from qdrant_client import models

from legger.retrieval import search as search_mod
from legger.retrieval.search import SearchHit, hybrid_search

DIM = 4

# The real lazy-singleton getter, captured before the autouse fixture below
# replaces the module attribute (the thread-safety test exercises the real one).
REAL_GET_SPARSE_QUERY_MODEL = search_mod._get_sparse_query_model


class FakeEmbedder:
    name = "fake"
    dim = DIM

    def __init__(self) -> None:
        self.queries: list[str] = []

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * DIM for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        self.queries.append(text)
        return [0.1, 0.2, 0.3, 0.4]


class FakeSparseModel:
    """Stands in for fastembed's SparseTextEmbedding at query time."""

    def query_embed(self, query: str):
        yield SimpleNamespace(indices=np.array([7, 42]), values=np.array([1.0, 1.0]))


def make_point(payload: dict[str, Any], score: float = 0.5) -> models.ScoredPoint:
    return models.ScoredPoint(
        id="00000000-0000-0000-0000-000000000001", version=0, score=score, payload=payload
    )


PAYLOAD = {
    "chunk_id": "codice-civile#art-2051#0",
    "act_ref": "codice-civile",
    "act_type": "codice",
    "act_title": "Codice civile",
    "article": "2051",
    "commi": ["1"],
    "collection": "Codici",
    "vigenza": "vigente",
    "file_path": "Codici/file.md",
    "header": "Codice civile\nArt. 2051 — Danno cagionato da cosa in custodia",
    "text": "Codice civile\nArt. 2051\n\nCiascuno è responsabile...",
}


class FakeQdrant:
    def __init__(self, points: list[models.ScoredPoint] | None = None) -> None:
        self.points = points if points is not None else [make_point(PAYLOAD)]
        self.calls: list[dict[str, Any]] = []

    def query_points(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(points=self.points)


@pytest.fixture(autouse=True)
def fake_sparse(monkeypatch: pytest.MonkeyPatch) -> FakeSparseModel:
    model = FakeSparseModel()
    monkeypatch.setattr(search_mod, "_get_sparse_query_model", lambda: model)
    return model


def run(client: FakeQdrant | None = None, **kwargs: Any) -> tuple[FakeQdrant, list[SearchHit]]:
    client = client or FakeQdrant()
    hits = hybrid_search(
        "art. 2051 c.c.",
        collection="norme_test",
        embedder=FakeEmbedder(),
        client=client,  # type: ignore[arg-type]
        **kwargs,
    )
    return client, hits


def test_request_has_two_prefetch_branches_with_rrf() -> None:
    client, _ = run(k=7, prefetch_k=33)
    (call,) = client.calls
    assert call["collection_name"] == "norme_test"
    assert call["limit"] == 7
    assert call["with_payload"] is True
    assert call["query"] == models.FusionQuery(fusion=models.Fusion.RRF)

    dense, sparse = call["prefetch"]
    assert dense.using == "dense"
    assert dense.limit == 33
    assert dense.query == [0.1, 0.2, 0.3, 0.4]
    assert sparse.using == "bm25"
    assert sparse.limit == 33
    assert isinstance(sparse.query, models.SparseVector)
    assert sparse.query.indices == [7, 42]
    assert sparse.query.values == [1.0, 1.0]


def test_default_vigenza_filter_inside_both_branches() -> None:
    client, _ = run()
    (call,) = client.calls
    expected = models.Filter(
        must=[models.FieldCondition(key="vigenza", match=models.MatchValue(value="vigente"))]
    )
    for branch in call["prefetch"]:
        assert branch.filter == expected


def test_custom_vigenza_value_is_used() -> None:
    client, _ = run(vigenza="multivigente")
    (call,) = client.calls
    condition = call["prefetch"][0].filter.must[0]
    assert condition.match == models.MatchValue(value="multivigente")


def test_vigenza_none_disables_filter() -> None:
    client, _ = run(vigenza=None)
    (call,) = client.calls
    for branch in call["prefetch"]:
        assert branch.filter is None


def test_search_hit_mapping_and_payload_passthrough() -> None:
    _, hits = run(client=FakeQdrant([make_point(PAYLOAD, score=0.9)]))
    (hit,) = hits
    assert hit.score == 0.9
    assert hit.chunk_id == "codice-civile#art-2051#0"
    assert hit.act_ref == "codice-civile"
    assert hit.article == "2051"
    assert hit.act_title == "Codice civile"
    assert hit.header.startswith("Codice civile")
    assert hit.text.startswith("Codice civile")
    assert hit.vigenza == "vigente"
    assert hit.payload == PAYLOAD  # full payload kept verbatim


def test_results_preserve_order() -> None:
    points = [make_point({**PAYLOAD, "article": str(n)}, score=1.0 / n) for n in (1, 2, 3)]
    _, hits = run(client=FakeQdrant(points))
    assert [h.article for h in hits] == ["1", "2", "3"]


def test_bm25_query_vector_uses_singleton_model(fake_sparse: FakeSparseModel) -> None:
    vec = search_mod.bm25_query_vector("oltraggio a pubblico ufficiale")
    assert isinstance(vec, models.SparseVector)
    assert vec.indices == [7, 42]


@pytest.mark.parametrize("query", ["", "   ", "\n\t "])
def test_empty_query_raises_value_error(query: str) -> None:
    client = FakeQdrant()
    with pytest.raises(ValueError, match="non-empty"):
        hybrid_search(
            query,
            collection="norme_test",
            embedder=FakeEmbedder(),
            client=client,  # type: ignore[arg-type]
        )
    assert client.calls == []  # rejected before any Qdrant call


class EmptySparseModel:
    """Sparse model whose query_embed yields nothing (e.g. all-stopword query)."""

    def query_embed(self, query: str):
        yield from ()


def test_bm25_query_vector_degrades_to_empty_sparse_vector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(search_mod, "_get_sparse_query_model", lambda: EmptySparseModel())
    vec = search_mod.bm25_query_vector("di a da in con su per")
    assert vec == models.SparseVector(indices=[], values=[])


def test_no_sparse_embedding_still_searches_dense_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(search_mod, "_get_sparse_query_model", lambda: EmptySparseModel())
    client, hits = run()
    (call,) = client.calls
    dense, sparse = call["prefetch"]
    assert dense.query == [0.1, 0.2, 0.3, 0.4]  # dense branch untouched
    assert sparse.query == models.SparseVector(indices=[], values=[])
    assert len(hits) == 1


def test_sparse_model_singleton_is_thread_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Concurrent first calls must construct the model exactly once."""
    import threading
    import time

    monkeypatch.setattr(search_mod, "_sparse_query_model", None)
    constructions = []
    gate = threading.Barrier(8)

    def slow_factory() -> FakeSparseModel:
        constructions.append(1)
        time.sleep(0.05)  # widen the race window: without the lock this multi-constructs
        return FakeSparseModel()

    monkeypatch.setattr(search_mod, "make_bm25_model", slow_factory)
    results: list[object] = []

    def worker() -> None:
        gate.wait()
        results.append(REAL_GET_SPARSE_QUERY_MODEL())

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(constructions) == 1
    assert all(r is results[0] for r in results)
