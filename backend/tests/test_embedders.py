"""Tests for legger.retrieval.embedders.

Unit tests mock the underlying model/client (no network, no model download).
The bge-m3 integration test is marked ``slow`` and excluded by default; run it
with ``uv run pytest -m slow``.
"""

import math
from typing import Any

import numpy as np
import pytest

from legger.retrieval.embedders import (
    BgeM3Embedder,
    Embedder,
    VoyageEmbedder,
    get_embedder,
)

DIM = 1024


class FakeBgeModel:
    """Stands in for BGEM3FlagModel; records encode() calls."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def encode(self, texts: list[str], **kwargs: Any) -> dict[str, np.ndarray]:
        self.calls.append({"texts": texts, **kwargs})
        return {"dense_vecs": np.full((len(texts), DIM), 0.5, dtype=np.float32)}


class FakeVoyageClient:
    """Stands in for voyageai.Client; records embed() calls."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def embed(self, texts: list[str], *, model: str, input_type: str) -> Any:
        self.calls.append({"texts": texts, "model": model, "input_type": input_type})

        class Result:
            embeddings = [[0.1] * DIM for _ in texts]

        return Result()


def make_voyage(**kwargs: Any) -> tuple[VoyageEmbedder, FakeVoyageClient]:
    embedder = VoyageEmbedder(api_key="pa-test", **kwargs)
    fake = FakeVoyageClient()
    embedder._client = fake
    return embedder, fake


# --- factory ---


def test_factory_dispatches_bge_m3() -> None:
    embedder = get_embedder("bge-m3")
    assert isinstance(embedder, BgeM3Embedder)
    assert embedder.name == "bge-m3"
    assert isinstance(embedder, Embedder)


def test_factory_dispatches_voyage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOYAGE_API_KEY", "pa-test")
    embedder = get_embedder("voyage-law-2")
    assert isinstance(embedder, VoyageEmbedder)
    assert embedder.name == "voyage-law-2"
    assert isinstance(embedder, Embedder)


def test_factory_rejects_unknown_name() -> None:
    with pytest.raises(ValueError, match="Unknown embedder"):
        get_embedder("ada-002")


# --- bge-m3 (mocked model) ---


def test_bge_empty_batch_skips_model_load() -> None:
    embedder = BgeM3Embedder()
    assert embedder.embed_documents([]) == []
    assert embedder._model is None  # lazy load must not have triggered


def test_bge_dims_and_batch_size() -> None:
    embedder = BgeM3Embedder()
    fake = FakeBgeModel()
    embedder._model = fake

    vectors = embedder.embed_documents(["a", "b", "c"])
    assert len(vectors) == 3
    assert all(len(v) == embedder.dim == DIM for v in vectors)
    assert fake.calls[0]["batch_size"] == 32
    assert fake.calls[0]["return_dense"] is True
    assert fake.calls[0]["return_sparse"] is False

    query_vec = embedder.embed_query("q")
    assert len(query_vec) == embedder.dim
    assert fake.calls[1]["texts"] == ["q"]


# --- voyage (mocked client) ---


def test_voyage_missing_key_raises_at_construction(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    with pytest.raises(ValueError, match="VOYAGE_API_KEY"):
        VoyageEmbedder(api_key="")


def test_voyage_empty_batch_makes_no_calls() -> None:
    embedder, fake = make_voyage()
    assert embedder.embed_documents([]) == []
    assert fake.calls == []


def test_voyage_batching_splits_at_128() -> None:
    embedder, fake = make_voyage()
    texts = [f"text {i}" for i in range(300)]

    vectors = embedder.embed_documents(texts)
    assert len(vectors) == 300
    assert all(len(v) == embedder.dim == DIM for v in vectors)
    assert [len(call["texts"]) for call in fake.calls] == [128, 128, 44]
    assert all(call["input_type"] == "document" for call in fake.calls)
    assert all(call["model"] == "voyage-law-2" for call in fake.calls)


def test_voyage_batching_splits_on_token_budget() -> None:
    """Token-aware batching: flush before the estimated budget is exceeded.

    Estimate is len(text) // 3; with 90-char texts (30 est. tokens each) and a
    100-token budget, exactly 3 texts fit per request (90 <= 100 < 120).
    """
    embedder, fake = make_voyage(max_tokens_per_batch=100)
    texts = [str(i) * 90 for i in range(7)]  # distinct texts, 30 est. tokens each

    vectors = embedder.embed_documents(texts)
    assert len(vectors) == 7
    assert [call["texts"] for call in fake.calls] == [texts[0:3], texts[3:6], texts[6:7]]


def test_voyage_single_text_over_budget_goes_alone() -> None:
    """A text whose estimate alone exceeds the budget is sent in its own
    request (Voyage truncates over-context inputs server-side)."""
    embedder, fake = make_voyage(max_tokens_per_batch=100)
    small_a, giant, small_b = "a" * 30, "g" * 600, "b" * 30  # 10, 200, 10 est. tokens

    vectors = embedder.embed_documents([small_a, giant, small_b])
    assert len(vectors) == 3
    assert [call["texts"] for call in fake.calls] == [[small_a], [giant], [small_b]]


def test_voyage_token_budget_defaults_per_model() -> None:
    """Budgets follow the per-model API caps (with margin); unknown voyage
    models fall back to the most conservative budget."""
    assert make_voyage()[0].max_tokens_per_batch == 100_000  # voyage-law-2 (120K cap)
    assert make_voyage(model="voyage-4-large")[0].max_tokens_per_batch == 100_000  # 120K cap
    assert make_voyage(model="voyage-4")[0].max_tokens_per_batch == 280_000  # 320K cap
    assert make_voyage(model="voyage-4-lite")[0].max_tokens_per_batch == 900_000  # 1M cap
    assert make_voyage(model="voyage-new-unknown")[0].max_tokens_per_batch == 100_000


def test_voyage_query_uses_query_input_type() -> None:
    embedder, fake = make_voyage()
    vector = embedder.embed_query("che cos'è un contratto?")
    assert len(vector) == embedder.dim
    assert len(fake.calls) == 1
    assert fake.calls[0]["input_type"] == "query"


def test_voyage_custom_model_and_batch() -> None:
    embedder, fake = make_voyage(model="voyage-4-large", batch_size=2)
    embedder.embed_documents(["a", "b", "c"])
    assert embedder.name == "voyage-4-large"
    assert [len(call["texts"]) for call in fake.calls] == [2, 1]


def test_voyage_client_built_with_max_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    """The SDK defaults max_retries to 0; the embedder must enable retries."""
    import voyageai

    captured: dict[str, Any] = {}

    def fake_client(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(voyageai, "Client", fake_client)
    embedder = VoyageEmbedder(api_key="pa-test")
    embedder._get_client()
    assert captured["max_retries"] == 5
    assert captured["api_key"] == "pa-test"


# --- integration (real model, run with: uv run pytest -m slow) ---


@pytest.mark.slow
def test_bge_m3_live_italian_legal_texts() -> None:
    documents = [
        # art. 1321 c.c. — relevant to the query
        "Il contratto è l'accordo di due o più parti per costituire, regolare o "
        "estinguere tra loro un rapporto giuridico patrimoniale.",
        # art. 832 c.c.
        "La proprietà è il diritto di godere e disporre delle cose in modo pieno ed "
        "esclusivo, entro i limiti e con l'osservanza degli obblighi stabiliti "
        "dall'ordinamento giuridico.",
        # d.P.R. 633/1972, art. 1 — irrelevant to the query
        "L'imposta sul valore aggiunto si applica sulle cessioni di beni e sulle "
        "prestazioni di servizi effettuate nel territorio dello Stato nell'esercizio "
        "di imprese o di arti e professioni.",
        # > 8k chars: must be truncated to the model window, not raise (the
        # chunker caps text at 8000 chars, close to bge-m3's 8192-token limit).
        "Le disposizioni del presente articolo si applicano a tutti i contratti. " * 120,
    ]
    query = "Che cosa si intende per contratto tra le parti?"

    embedder = get_embedder("bge-m3")
    doc_vecs = embedder.embed_documents(documents)
    query_vec = embedder.embed_query(query)

    assert len(documents[3]) > 8000
    assert len(doc_vecs) == 4
    assert all(len(v) == embedder.dim for v in doc_vecs)
    assert len(query_vec) == embedder.dim

    def cosine(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b, strict=True))
        return dot / (math.hypot(*a) * math.hypot(*b))

    sim_relevant = cosine(query_vec, doc_vecs[0])
    sim_irrelevant = cosine(query_vec, doc_vecs[2])
    assert sim_relevant > sim_irrelevant
