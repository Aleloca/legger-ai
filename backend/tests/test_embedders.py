"""Tests for legger.retrieval.embedders.

Unit tests mock the underlying model/client (no network, no model download).
The bge-m3 integration test is marked ``slow`` and excluded by default; run it
with ``uv run pytest -m slow``.
"""

import logging
import math
from collections.abc import Callable
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


def make_voyage(
    token_counter: Callable[[list[str]], list[int]] | None = None,
    **kwargs: Any,
) -> tuple[VoyageEmbedder, FakeVoyageClient]:
    """Build a VoyageEmbedder with a fake client and an injected token counter.

    The counter replaces ``_count_tokens`` so unit tests never load the real
    HF tokenizer (no network, no cache dependence). Default: 1 token per text,
    which makes token budgets irrelevant unless a test injects its own.
    """
    embedder = VoyageEmbedder(api_key="pa-test", **kwargs)
    fake = FakeVoyageClient()
    embedder._client = fake
    if token_counter is None:
        token_counter = lambda texts: [1] * len(texts)  # noqa: E731
    embedder._count_tokens = token_counter  # type: ignore[method-assign]
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
    """Token-aware packing flushes before the exact-count budget is exceeded,
    and the counter runs exactly once per embed_documents call."""
    counts = {"a": 40, "b": 40, "c": 30, "d": 80, "e": 10}
    counter_calls: list[list[str]] = []

    def counter(texts: list[str]) -> list[int]:
        counter_calls.append(texts)
        return [counts[t] for t in texts]

    embedder, fake = make_voyage(token_counter=counter, max_tokens_per_batch=100)
    texts = ["a", "b", "c", "d", "e"]

    vectors = embedder.embed_documents(texts)
    assert len(vectors) == 5
    # a+b=80 fits; +c would hit 110 > 100; c+d would hit 110 > 100; d+e=90 fits.
    assert [call["texts"] for call in fake.calls] == [["a", "b"], ["c"], ["d", "e"]]
    assert counter_calls == [texts]  # counted once, over the full input


def test_voyage_single_text_over_budget_goes_alone() -> None:
    """A text whose exact count alone exceeds the budget is sent in its own
    request (Voyage truncates over-context inputs server-side)."""
    counts = {"small_a": 10, "giant": 200, "small_b": 10}
    embedder, fake = make_voyage(
        token_counter=lambda texts: [counts[t] for t in texts],
        max_tokens_per_batch=100,
    )

    vectors = embedder.embed_documents(["small_a", "giant", "small_b"])
    assert len(vectors) == 3
    assert [call["texts"] for call in fake.calls] == [["small_a"], ["giant"], ["small_b"]]


def test_voyage_fallback_estimate_when_tokenizer_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No tokenizer (offline/HF down) -> len//2 estimates, same packing logic."""
    monkeypatch.setattr(VoyageEmbedder, "_get_tokenizer", lambda self: None)
    embedder = VoyageEmbedder(api_key="pa-test", max_tokens_per_batch=100)
    fake = FakeVoyageClient()
    embedder._client = fake  # type: ignore[assignment]
    texts = [str(i) * 90 for i in range(5)]  # 45 est. tokens each -> 2 per batch

    vectors = embedder.embed_documents(texts)
    assert len(vectors) == 5
    assert [call["texts"] for call in fake.calls] == [texts[0:2], texts[2:4], texts[4:5]]


def test_voyage_tokenizer_load_failure_warns_and_never_crashes(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A tokenizer load error (offline, HF down) logs a warning and falls back
    to estimates; embed_documents still succeeds. The failure is remembered,
    so the load is not retried on every call."""
    import tokenizers

    load_attempts: list[str] = []

    def boom(identifier: str, **kwargs: Any) -> Any:
        load_attempts.append(identifier)
        raise OSError("HF Hub unreachable")

    monkeypatch.setattr(tokenizers.Tokenizer, "from_pretrained", staticmethod(boom))
    embedder = VoyageEmbedder(api_key="pa-test")
    fake = FakeVoyageClient()
    embedder._client = fake  # type: ignore[assignment]

    with caplog.at_level(logging.WARNING, logger="legger.retrieval.embedders"):
        vectors = embedder.embed_documents(["a" * 30, "b" * 30])
        embedder.embed_documents(["c" * 30, "d" * 30])

    assert len(vectors) == 2
    assert "falling back" in caplog.text
    assert load_attempts == ["voyageai/voyage-law-2"]  # not retried per call


def test_voyage_token_budget_defaults_per_model() -> None:
    """Budgets follow the per-model API caps with ~8% headroom (counts are
    exact, headroom covers server-side drift); unknown voyage models fall
    back to the most conservative budget."""
    assert make_voyage()[0].max_tokens_per_batch == 110_000  # voyage-law-2 (120K cap)
    assert make_voyage(model="voyage-4-large")[0].max_tokens_per_batch == 110_000  # 120K cap
    assert make_voyage(model="voyage-4")[0].max_tokens_per_batch == 295_000  # 320K cap
    assert make_voyage(model="voyage-4-lite")[0].max_tokens_per_batch == 920_000  # 1M cap
    assert make_voyage(model="voyage-new-unknown")[0].max_tokens_per_batch == 110_000


def test_voyage_query_uses_query_input_type() -> None:
    # Plain embedder (no injected counter): the single-text shortcut must keep
    # the query path tokenizer-free.
    embedder = VoyageEmbedder(api_key="pa-test")
    fake = FakeVoyageClient()
    embedder._client = fake  # type: ignore[assignment]

    vector = embedder.embed_query("che cos'è un contratto?")
    assert len(vector) == embedder.dim
    assert len(fake.calls) == 1
    assert fake.calls[0]["input_type"] == "query"
    assert embedder._tokenizer is None  # never loaded for a single text


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
