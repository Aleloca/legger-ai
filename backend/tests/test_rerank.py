"""Unit tests for legger.retrieval.rerank (mocked cross-encoder, no model).

Pins down: ordering by cross-encoder score (desc, stable on ties), the top_k
cut, score attachment without mutating the inputs, the lazy thread-safe
singleton, the scalar-score quirk of FlagReranker for single pairs, and the
device policy (LEGGER_RERANK_DEVICE override, cpu-on-darwin default).
"""

from __future__ import annotations

import threading

import pytest

import legger.retrieval.rerank as rerank_module
from legger.retrieval.rerank import _rerank_device, rerank
from legger.retrieval.search import SearchHit


def make_hit(text: str, score: float = 0.5) -> SearchHit:
    return SearchHit(
        score=score,
        chunk_id=f"act#{text}#0",
        act_ref="codice-civile",
        article="2051",
        act_title="Codice civile",
        header="Codice civile\nArt. 2051",
        text=text,
        vigenza="vigente",
        payload={},
    )


class FakeReranker:
    """compute_score stub: per-text canned scores, call/pair bookkeeping."""

    def __init__(self, scores_by_text: dict[str, float]) -> None:
        self.scores_by_text = scores_by_text
        self.calls: list[list[tuple[str, str]]] = []

    def compute_score(self, sentence_pairs: list[tuple[str, str]]) -> list[float] | float:
        self.calls.append(sentence_pairs)
        scores = [self.scores_by_text[passage] for _query, passage in sentence_pairs]
        if len(scores) == 1:  # FlagReranker returns a bare float for one pair
            return scores[0]
        return scores


@pytest.fixture(autouse=True)
def reset_singleton(monkeypatch: pytest.MonkeyPatch):
    """Each test starts without a cached model and never loads the real one."""
    monkeypatch.setattr(rerank_module, "_reranker", None)

    def boom() -> None:
        raise AssertionError("the real FlagReranker must never be constructed in unit tests")

    monkeypatch.setattr(rerank_module, "_make_reranker", boom)
    yield
    rerank_module._reranker = None


def install_fake(monkeypatch: pytest.MonkeyPatch, scores_by_text: dict[str, float]) -> FakeReranker:
    fake = FakeReranker(scores_by_text)
    monkeypatch.setattr(rerank_module, "_make_reranker", lambda: fake)
    return fake


# --- ordering / top_k / scores -------------------------------------------------


def test_reorders_by_cross_encoder_score_desc(monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake(monkeypatch, {"a": 0.1, "b": 0.9, "c": 0.5})
    hits = [make_hit("a"), make_hit("b"), make_hit("c")]
    out = rerank("query", hits)
    assert [h.text for h in out] == ["b", "c", "a"]
    assert [h.score for h in out] == [0.9, 0.5, 0.1]


def test_top_k_cut(monkeypatch: pytest.MonkeyPatch) -> None:
    scores = {f"t{i}": i / 100 for i in range(50)}
    install_fake(monkeypatch, scores)
    hits = [make_hit(f"t{i}") for i in range(50)]
    out = rerank("query", hits, top_k=10)
    assert len(out) == 10
    assert [h.text for h in out] == [f"t{i}" for i in range(49, 39, -1)]


def test_ties_keep_upstream_order(monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake(monkeypatch, {"first": 0.5, "second": 0.5, "third": 0.5})
    out = rerank("query", [make_hit("first"), make_hit("second"), make_hit("third")])
    assert [h.text for h in out] == ["first", "second", "third"]  # stable sort


def test_inputs_not_mutated_and_pairs_built_from_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = install_fake(monkeypatch, {"a": 0.9})
    hit = make_hit("a", score=0.123)
    out = rerank("la query", [hit])
    assert hit.score == 0.123  # original untouched
    assert out[0].score == 0.9
    assert out[0].chunk_id == hit.chunk_id  # everything else carried over
    assert fake.calls == [[("la query", "a")]]


def test_single_pair_scalar_score_is_handled(monkeypatch: pytest.MonkeyPatch) -> None:
    # FakeReranker returns a bare float for one pair, like FlagReranker does.
    install_fake(monkeypatch, {"only": 0.7})
    out = rerank("query", [make_hit("only")])
    assert len(out) == 1
    assert out[0].score == 0.7


def test_empty_hits_short_circuit_without_model(monkeypatch: pytest.MonkeyPatch) -> None:
    # autouse fixture makes _make_reranker explode: no model must be touched.
    assert rerank("query", []) == []


def test_empty_query_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError):
        rerank("   ", [make_hit("a")])


# --- lazy singleton -------------------------------------------------------------


def test_model_is_loaded_lazily_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}
    fake = FakeReranker({"a": 0.4, "b": 0.6})

    def factory() -> FakeReranker:
        calls["n"] += 1
        return fake

    monkeypatch.setattr(rerank_module, "_make_reranker", factory)
    assert calls["n"] == 0  # nothing loaded at import/patch time
    rerank("query", [make_hit("a"), make_hit("b")])
    rerank("query", [make_hit("a"), make_hit("b")])
    assert calls["n"] == 1  # singleton: one construction across calls


def test_singleton_is_thread_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}
    started = threading.Barrier(8)

    def factory() -> FakeReranker:
        calls["n"] += 1
        return FakeReranker({"a": 0.5})

    monkeypatch.setattr(rerank_module, "_make_reranker", factory)

    def worker() -> None:
        started.wait()
        rerank_module._get_reranker()

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert calls["n"] == 1


# --- device policy ---------------------------------------------------------------


def test_device_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEGGER_RERANK_DEVICE", "cuda")
    assert _rerank_device() == "cuda"


def test_darwin_defaults_to_cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LEGGER_RERANK_DEVICE", raising=False)
    monkeypatch.setattr(rerank_module.sys, "platform", "darwin")
    assert _rerank_device() == "cpu"


def test_invalid_override_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEGGER_RERANK_DEVICE", "gpu9000")
    monkeypatch.setattr(rerank_module.sys, "platform", "darwin")
    assert _rerank_device() == "cpu"  # warning logged, darwin default applies


def test_non_darwin_uses_embedder_autodetect(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LEGGER_RERANK_DEVICE", raising=False)
    monkeypatch.setattr(rerank_module.sys, "platform", "linux")
    monkeypatch.setattr(rerank_module, "_detect_device", lambda: "cuda")
    assert _rerank_device() == "cuda"
