"""Unit tests for legger.retrieval.index (mocked Qdrant client, no network).

The real Codici indexing run is the integration test for this module; here we
pin down the deterministic parts: collection naming, point id derivation,
payload completeness, collection schema calls and the lot retry contract.
"""

import uuid
from typing import Any

import numpy as np
import pytest
from qdrant_client import models

from legger.corpus.chunker import Chunk
from legger.retrieval.index import (
    DENSE_VECTOR,
    KEYWORD_INDEXES,
    SPARSE_VECTOR,
    chunk_payload,
    embedder_slug,
    ensure_collection,
    index_chunks,
    point_id,
    qdrant_collection_name,
)

DIM = 4


def make_chunk(i: int = 0) -> Chunk:
    return Chunk(
        id=f"codice-civile#art-2051#{i}",
        act_ref="codice-civile",
        act_type="codice",
        act_title="Codice civile",
        article="2051",
        commi=["1"],
        collection="Codici",
        vigenza="vigente",
        file_path="Codici/1942-04-04_042U0262_VIGENZA_2026-04-29_V0.md",
        header="Codice civile\nArt. 2051 — Danno cagionato da cosa in custodia",
        text="Codice civile\nArt. 2051\n\nCiascuno è responsabile del danno...",
    )


class FakeQdrant:
    """Records calls; configurable existence."""

    def __init__(self, exists: bool = False) -> None:
        self._exists = exists
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def collection_exists(self, name: str) -> bool:
        return self._exists

    def create_collection(self, **kwargs: Any) -> None:
        self.calls.append(("create_collection", kwargs))
        self._exists = True

    def delete_collection(self, name: str) -> None:
        self.calls.append(("delete_collection", {"name": name}))
        self._exists = False

    def create_payload_index(self, **kwargs: Any) -> None:
        self.calls.append(("create_payload_index", kwargs))

    def upsert(self, **kwargs: Any) -> None:
        self.calls.append(("upsert", kwargs))

    def named(self, name: str) -> list[dict[str, Any]]:
        return [kwargs for called, kwargs in self.calls if called == name]


class FakeEmbedder:
    name = "fake"
    dim = DIM

    def __init__(self, fail_times: int = 0) -> None:
        self.fail_times = fail_times
        self.calls: list[list[str]] = []

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(texts)
        if self.fail_times > 0:
            self.fail_times -= 1
            raise ConnectionError("boom")
        return [[0.1] * DIM for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        return [0.1] * DIM


class FakeSparse:
    """Stands in for fastembed SparseTextEmbedding."""

    class _Vec:
        indices = np.array([7, 42])
        values = np.array([1.5, 0.5])

    def embed(self, texts: list[str]) -> list[Any]:
        return [self._Vec() for _ in texts]


# --- naming ---


def test_embedder_slug() -> None:
    assert embedder_slug("bge-m3") == "bgem3"
    assert embedder_slug("voyage-law-2") == "voyagelaw2"
    assert embedder_slug("voyage-4-large") == "voyage4large"


def test_qdrant_collection_name() -> None:
    assert qdrant_collection_name("bge-m3") == "norme_bgem3"
    assert qdrant_collection_name("voyage-law-2", "exp1") == "norme_voyagelaw2_exp1"


# --- point ids ---


def test_point_id_is_deterministic_uuid5() -> None:
    chunk_id = "codice-civile#art-2051#0"
    assert point_id(chunk_id) == point_id(chunk_id)
    assert point_id(chunk_id) == str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_id))
    assert point_id(chunk_id) != point_id("codice-civile#art-2051#1")


# --- payload ---


def test_payload_has_all_chunk_fields_with_chunk_id() -> None:
    payload = chunk_payload(make_chunk())
    assert payload == {
        "chunk_id": "codice-civile#art-2051#0",
        "act_ref": "codice-civile",
        "act_type": "codice",
        "act_title": "Codice civile",
        "article": "2051",
        "commi": ["1"],
        "collection": "Codici",
        "vigenza": "vigente",
        "file_path": "Codici/1942-04-04_042U0262_VIGENZA_2026-04-29_V0.md",
        "header": "Codice civile\nArt. 2051 — Danno cagionato da cosa in custodia",
        "text": "Codice civile\nArt. 2051\n\nCiascuno è responsabile del danno...",
    }
    assert "id" not in payload  # renamed, never duplicated


# --- ensure_collection ---


def test_ensure_collection_creates_schema_and_indexes() -> None:
    client = FakeQdrant(exists=False)
    ensure_collection(client, "norme_test", DIM)  # type: ignore[arg-type]

    [create] = client.named("create_collection")
    dense = create["vectors_config"][DENSE_VECTOR]
    assert dense.size == DIM
    assert dense.distance == models.Distance.COSINE
    sparse = create["sparse_vectors_config"][SPARSE_VECTOR]
    assert sparse.modifier == models.Modifier.IDF

    indexes = client.named("create_payload_index")
    assert {idx["field_name"] for idx in indexes} == set(KEYWORD_INDEXES)
    assert all(idx["field_schema"] == models.PayloadSchemaType.KEYWORD for idx in indexes)


def test_ensure_collection_recreate_drops_first() -> None:
    client = FakeQdrant(exists=True)
    ensure_collection(client, "norme_test", DIM, recreate=True)  # type: ignore[arg-type]
    assert [name for name, _ in client.calls[:2]] == ["delete_collection", "create_collection"]


# --- index_chunks ---


def test_index_chunks_upserts_points_in_lots() -> None:
    client = FakeQdrant()
    chunks = [make_chunk(i) for i in range(5)]
    embedder = FakeEmbedder()

    count = index_chunks(client, "norme_test", chunks, embedder, FakeSparse(), lot_size=2)  # type: ignore[arg-type]

    assert count == 5
    upserts = client.named("upsert")
    assert [len(up["points"]) for up in upserts] == [2, 2, 1]
    assert all(up["wait"] is True for up in upserts)

    point = upserts[0]["points"][0]
    assert point.id == point_id(chunks[0].id)
    assert point.payload["chunk_id"] == chunks[0].id
    assert point.payload["text"] == chunks[0].text
    assert point.vector[DENSE_VECTOR] == [0.1] * DIM
    assert point.vector[SPARSE_VECTOR] == models.SparseVector(indices=[7, 42], values=[1.5, 0.5])


def test_index_chunks_retries_failed_lot_once() -> None:
    client = FakeQdrant()
    chunks = [make_chunk(i) for i in range(3)]
    embedder = FakeEmbedder(fail_times=1)

    count = index_chunks(client, "norme_test", chunks, embedder, FakeSparse(), lot_size=2)  # type: ignore[arg-type]

    assert count == 3
    # First lot embedded twice (failure + retry), second lot once.
    assert [len(texts) for texts in embedder.calls] == [2, 2, 1]
    assert len(client.named("upsert")) == 2


def test_index_chunks_aborts_after_second_failure() -> None:
    client = FakeQdrant()
    chunks = [make_chunk(i) for i in range(2)]
    embedder = FakeEmbedder(fail_times=2)

    with pytest.raises(RuntimeError, match="re-run"):
        index_chunks(client, "norme_test", chunks, embedder, FakeSparse(), lot_size=2)  # type: ignore[arg-type]
    assert client.named("upsert") == []  # nothing upserted for the failed lot
