"""Tests for legger.retrieval.pipeline (Task E5): the unified pipeline.

Every I/O stage is mocked (understand_query, resolve_refs, hybrid_search,
rerank, follow_citations) — what is under test is the pipeline's
own LOGIC: routing (explicit refs -> fastpath first), act_ref=None binding
from context, act-level supplement capping, merge/dedup/cap rules, the
rerank toggle wiring, the vigenza filter policy, citation appending and
budget passthrough, the degrade-on-failure policy, and the sources list.

:func:`~legger.retrieval.fastpath.extract_refs` stays REAL: it is pure
(no I/O), already covered by its own test table, and using it keeps the
routing scenarios honest — the binding tests exercise the actual unbound
refs the grammar emits.
"""

from types import SimpleNamespace
from typing import Any

import pytest

from legger.chat.understanding import QueryAnalysis
from legger.retrieval import pipeline
from legger.retrieval.pipeline import (
    ACT_SUPPLEMENT_CAP,
    MERGE_OVERFLOW,
    RERANK_CANDIDATES,
    retrieve,
)
from legger.retrieval.search import SearchHit


def make_hit(
    chunk_id: str,
    act_ref: str = "codice-civile",
    article: str = "2051",
    score: float = 0.5,
    text: str = "testo del passaggio normativo",
    vigenza: str = "vigente",
    title: str = "Codice civile",
) -> SearchHit:
    return SearchHit(
        score=score,
        chunk_id=chunk_id,
        act_ref=act_ref,
        article=article,
        act_title=title,
        header=f"[{act_ref}] art. {article}",
        text=text,
        vigenza=vigenza,
        payload={},
    )


class Harness:
    """Installs recording fakes for every pipeline stage; per-test overridable."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # --- behavior knobs ------------------------------------------------
        self.analysis: QueryAnalysis | None = None  # None -> echo the user message
        self.understand_error: Exception | None = None
        self.resolve_article_returns: list[SearchHit] = []
        self.resolve_act_returns: list[SearchHit] = []
        self.resolve_error: Exception | None = None
        self.hybrid_returns: list[SearchHit] = [make_hit("hy#0"), make_hit("hy#1")]
        self.hybrid_error: Exception | None = None
        self.follow_returns: list[SearchHit] = []
        self.follow_error: Exception | None = None
        self.rerank_enabled = False
        self.rerank_returns: list[SearchHit] | None = None  # None -> reversed[:top_k]
        self.rerank_error: Exception | None = None
        # --- call records --------------------------------------------------
        self.understand_calls: list[list[dict]] = []
        self.understand_kwargs: list[dict] = []
        self.resolve_calls: list[list[Any]] = []
        self.hybrid_calls: list[dict] = []
        self.rerank_calls: list[dict] = []
        self.follow_calls: list[dict] = []
        # --- sentinels ------------------------------------------------------
        self.qdrant = object()
        self.engine = object()
        self.anthropic = object()
        self.embedder = object()

        def fake_understand(messages, *, anthropic_client, model=None, effort=None):
            assert anthropic_client is self.anthropic
            self.understand_calls.append(messages)
            self.understand_kwargs.append({"model": model, "effort": effort})
            if self.understand_error is not None:
                raise self.understand_error
            if self.analysis is not None:
                return self.analysis
            last = next(m["content"] for m in reversed(messages) if m["role"] == "user")
            return QueryAnalysis(rewritten_query=last)

        def fake_resolve(refs, *, qdrant_client, collection, engine=None):
            assert qdrant_client is self.qdrant
            assert engine is self.engine
            self.resolve_calls.append(list(refs))
            if self.resolve_error is not None:
                raise self.resolve_error
            if refs and refs[0].article is None:
                return list(self.resolve_act_returns)
            return list(self.resolve_article_returns)

        def fake_hybrid(query, *, collection, embedder, client, k, vigenza="vigente"):
            assert client is self.qdrant and embedder is self.embedder
            self.hybrid_calls.append({"query": query, "k": k, "vigenza": vigenza})
            if self.hybrid_error is not None:
                raise self.hybrid_error
            return list(self.hybrid_returns)

        def fake_rerank(query, hits, *, top_k):
            self.rerank_calls.append({"query": query, "hits": list(hits), "top_k": top_k})
            if self.rerank_error is not None:
                raise self.rerank_error
            if self.rerank_returns is not None:
                return list(self.rerank_returns)
            return list(reversed(hits))[:top_k]

        def fake_follow(hits, *, qdrant_client, collection, engine=None, token_budget):
            assert qdrant_client is self.qdrant
            assert engine is self.engine
            self.follow_calls.append({"hits": list(hits), "token_budget": token_budget})
            if self.follow_error is not None:
                raise self.follow_error
            return list(self.follow_returns)

        monkeypatch.setattr(pipeline, "understand_query", fake_understand)
        monkeypatch.setattr(pipeline, "resolve_refs", fake_resolve)
        monkeypatch.setattr(pipeline, "hybrid_search", fake_hybrid)
        monkeypatch.setattr(pipeline, "rerank", fake_rerank)
        monkeypatch.setattr(pipeline, "follow_citations", fake_follow)

    _UNSET = object()

    def run(
        self,
        messages: list[dict],
        *,
        k: int = 10,
        citation_budget: int = 4000,
        rerank_enabled: Any = _UNSET,
        qu_model: str | None = None,
        qu_effort: str | None = None,
    ):
        if rerank_enabled is self._UNSET:
            rerank_enabled = self.rerank_enabled
        return retrieve(
            messages,
            qdrant_client=self.qdrant,
            engine=self.engine,
            anthropic_client=self.anthropic,
            collection="norme",
            embedder=self.embedder,
            k=k,
            citation_budget=citation_budget,
            rerank_enabled=rerank_enabled,
            qu_model=qu_model,
            qu_effort=qu_effort,
        )


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> Harness:
    return Harness(monkeypatch)


def user(content: str) -> dict:
    return {"role": "user", "content": content}


def assistant(content: str) -> dict:
    return {"role": "assistant", "content": content}


# ---------------------------------------------------------------------------
# Routing: explicit refs -> fastpath hits FIRST
# ---------------------------------------------------------------------------


def test_explicit_ref_fastpath_hits_go_first(harness: Harness) -> None:
    fp = make_hit("codice-civile#art-2051#0", article="2051")
    harness.resolve_article_returns = [fp]
    result = harness.run([user("cosa dice l'art. 2051 c.c.?")])
    assert [h.chunk_id for h in result.hits] == ["codice-civile#art-2051#0", "hy#0", "hy#1"]
    assert result.used_fastpath is True
    [refs] = harness.resolve_calls
    assert (refs[0].act_ref, refs[0].article) == ("codice-civile", "2051")


def test_no_refs_means_hybrid_only(harness: Harness) -> None:
    result = harness.run([user("responsabilità per danni da cose in custodia")])
    assert harness.resolve_calls == []
    assert [h.chunk_id for h in result.hits] == ["hy#0", "hy#1"]
    assert result.used_fastpath is False
    assert result.query_analysis is not None


def test_extract_refs_falls_back_to_rewritten_query(harness: Harness) -> None:
    """No refs in the original message -> the rewritten query is extracted."""
    harness.analysis = QueryAnalysis(rewritten_query="cosa dice l'art. 275 c.p.p.?")
    harness.resolve_article_returns = [make_hit("dpr-447-1988#art-275#0", act_ref="dpr-447-1988")]
    result = harness.run([user("e l'articolo successivo?")])
    [refs] = harness.resolve_calls
    assert (refs[0].act_ref, refs[0].article) == ("dpr-447-1988", "275")
    assert result.used_fastpath is True


def test_extract_refs_on_original_wins_over_rewritten(harness: Harness) -> None:
    """The user's own estremi beat whatever the rewrite produced (E2 guidance)."""
    harness.analysis = QueryAnalysis(rewritten_query="art. 9999 del codice penale")
    harness.run([user("cosa dice l'art. 2051 c.c.?")])
    [refs] = harness.resolve_calls
    assert [(r.act_ref, r.article) for r in refs] == [("codice-civile", "2051")]


# ---------------------------------------------------------------------------
# act_ref=None binding
# ---------------------------------------------------------------------------


def test_unbound_ref_binds_to_single_act_from_other_refs(harness: Harness) -> None:
    harness.run(
        [user("confronta l'articolo 14 del d.lgs. 81/2008 con quanto previsto dall'articolo 26")]
    )
    [refs] = harness.resolve_calls
    assert [(r.act_ref, r.article) for r in refs] == [
        ("dlgs-81-2008", "14"),
        ("dlgs-81-2008", "26"),
    ]


def test_unbound_ref_binds_from_assistant_markers(harness: Harness) -> None:
    messages = [
        user("quando scattano le misure cautelari?"),
        assistant("Le esigenze cautelari sono all'art. 274 [[dpr-447-1988|art.274]]."),
        user("cosa dice l'articolo 275?"),
    ]
    harness.run(messages)
    [refs] = harness.resolve_calls
    assert [(r.act_ref, r.article) for r in refs] == [("dpr-447-1988", "275")]


def test_unbound_ref_dropped_without_context(harness: Harness) -> None:
    """No other refs, no previous assistant turn -> the ref is dropped."""
    result = harness.run([user("cosa dice l'articolo 275?")])
    assert harness.resolve_calls == []
    assert result.used_fastpath is False


def test_unbound_ref_dropped_with_ambiguous_markers(harness: Harness) -> None:
    messages = [
        user("custodia e circolazione"),
        assistant("Vedi [[codice-civile|art.2051]] e [[dlgs-285-1992|art.140]]."),
        user("cosa dice l'articolo 9?"),
    ]
    result = harness.run(messages)
    assert harness.resolve_calls == []
    assert result.used_fastpath is False


def test_unbound_ref_dropped_with_multiple_acts_in_refs(harness: Harness) -> None:
    """2+ distinct acts among the other refs -> ambiguous, drop the unbound one."""
    harness.run(
        [
            user(
                "art. 14 del d.lgs. 81/2008 e art. 2051 c.c., "
                "ma cosa prevede al riguardo l'articolo 9?"
            )
        ]
    )
    [refs] = harness.resolve_calls
    assert [(r.act_ref, r.article) for r in refs] == [
        ("dlgs-81-2008", "14"),
        ("codice-civile", "2051"),
    ]


def test_binding_uses_last_assistant_turn_only(harness: Harness) -> None:
    """Markers in OLDER assistant turns do not bind (only the previous one)."""
    messages = [
        user("custodia"),
        assistant("Vedi [[codice-civile|art.2051]]."),
        user("circolazione stradale"),
        assistant("Nessuna norma specifica trovata nel contesto."),  # no markers
        user("cosa dice l'articolo 9?"),
    ]
    result = harness.run(messages)
    assert harness.resolve_calls == []
    assert result.used_fastpath is False


# ---------------------------------------------------------------------------
# Act-level supplements
# ---------------------------------------------------------------------------


def test_act_level_hits_are_supplements_capped_at_two(harness: Harness) -> None:
    harness.resolve_act_returns = [
        make_hit(f"dlgs-81-2008#art-{i}#0", act_ref="dlgs-81-2008", article=str(i))
        for i in range(1, 6)
    ]
    result = harness.run([user("cosa prevede il d.lgs. 81/2008?")])
    assert [h.chunk_id for h in result.hits] == [
        "hy#0",
        "hy#1",
        "dlgs-81-2008#art-1#0",
        "dlgs-81-2008#art-2#0",
    ]
    assert len([h for h in result.hits if h.act_ref == "dlgs-81-2008"]) == ACT_SUPPLEMENT_CAP
    assert result.used_fastpath is True
    [refs] = harness.resolve_calls
    assert [(r.act_ref, r.article) for r in refs] == [("dlgs-81-2008", None)]


# ---------------------------------------------------------------------------
# Merge: dedup + cap
# ---------------------------------------------------------------------------


def test_merge_dedup_by_chunk_id_keeps_fastpath_position(harness: Harness) -> None:
    shared = make_hit("codice-civile#art-2051#0")
    harness.resolve_article_returns = [shared.model_copy(update={"score": 1.0})]
    harness.hybrid_returns = [shared, make_hit("hy#1", article="2052")]
    result = harness.run([user("art. 2051 c.c.")])
    assert [h.chunk_id for h in result.hits] == ["codice-civile#art-2051#0", "hy#1"]
    assert result.hits[0].score == 1.0  # the fastpath copy won, not the hybrid one


def test_merge_caps_at_k_plus_overflow(harness: Harness) -> None:
    harness.resolve_article_returns = [make_hit(f"codice-civile#art-2051#{i}") for i in range(4)]
    harness.hybrid_returns = [make_hit(f"hy#{i}", article=str(i)) for i in range(5)]
    result = harness.run([user("art. 2051 c.c.")], k=3)
    assert len(result.hits) == 3 + MERGE_OVERFLOW
    # fastpath wins survive in front; hybrid fills the remaining slots
    assert [h.chunk_id for h in result.hits[:4]] == [
        f"codice-civile#art-2051#{i}" for i in range(4)
    ]


# ---------------------------------------------------------------------------
# Rerank toggle wiring
# ---------------------------------------------------------------------------


def test_rerank_disabled_fetches_k_and_skips_reranker(harness: Harness) -> None:
    harness.rerank_enabled = False
    harness.run([user("responsabilità del custode")], k=7)
    assert harness.hybrid_calls == [
        {"query": "responsabilità del custode", "k": 7, "vigenza": "vigente"}
    ]
    assert harness.rerank_calls == []


def test_rerank_enabled_widens_pool_then_cuts_to_k(harness: Harness) -> None:
    harness.rerank_enabled = True
    harness.hybrid_returns = [make_hit(f"hy#{i}", article=str(i)) for i in range(6)]
    result = harness.run([user("responsabilità del custode")], k=3)
    assert harness.hybrid_calls[0]["k"] == RERANK_CANDIDATES
    [call] = harness.rerank_calls
    assert call["top_k"] == 3
    assert call["hits"] == harness.hybrid_returns
    # the fake reranker reverses: the pipeline must use ITS output
    assert [h.chunk_id for h in result.hits] == ["hy#5", "hy#4", "hy#3"]


def test_rerank_default_none_reads_settings(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """rerank_enabled=None (the production default) falls back to Settings."""
    monkeypatch.setattr(pipeline, "Settings", lambda: SimpleNamespace(rerank_enabled=True))
    harness.run([user("responsabilità del custode")], k=3, rerank_enabled=None)
    assert harness.hybrid_calls[0]["k"] == RERANK_CANDIDATES
    assert len(harness.rerank_calls) == 1


def test_rerank_failure_degrades_to_rrf_order(harness: Harness) -> None:
    harness.rerank_enabled = True
    harness.rerank_error = RuntimeError("model wedged")
    harness.hybrid_returns = [make_hit(f"hy#{i}", article=str(i)) for i in range(6)]
    result = harness.run([user("responsabilità del custode")], k=3)
    assert [h.chunk_id for h in result.hits] == ["hy#0", "hy#1", "hy#2"]


# ---------------------------------------------------------------------------
# Vigenza policy
# ---------------------------------------------------------------------------


def test_default_vigenza_filter_is_vigente(harness: Harness) -> None:
    harness.run([user("responsabilità del custode")])
    assert harness.hybrid_calls[0]["vigenza"] == "vigente"


def test_wants_historical_disables_vigenza_filter(harness: Harness) -> None:
    harness.analysis = QueryAnalysis(
        rewritten_query="art. 18 statuto dei lavoratori prima della riforma Fornero",
        wants_historical=True,
        temporal_reference="2012",
    )
    harness.run([user("com'era l'art. 18 prima della Fornero?")])
    assert harness.hybrid_calls[0]["vigenza"] is None


# ---------------------------------------------------------------------------
# Query understanding wiring
# ---------------------------------------------------------------------------


def test_hybrid_uses_rewritten_query(harness: Harness) -> None:
    harness.analysis = QueryAnalysis(rewritten_query="responsabilità da cose in custodia")
    harness.run([user("e in quel caso chi paga?")])
    assert harness.hybrid_calls[0]["query"] == "responsabilità da cose in custodia"


def test_blank_rewritten_query_falls_back_to_original(harness: Harness) -> None:
    harness.analysis = QueryAnalysis(rewritten_query="   ")
    harness.run([user("responsabilità del custode")])
    assert harness.hybrid_calls[0]["query"] == "responsabilità del custode"


def test_qu_model_and_effort_passed_through(harness: Harness) -> None:
    # Beta-testing config: qu_model/qu_effort flow into understand_query.
    harness.run([user("q")], qu_model="claude-sonnet-4-6", qu_effort="low")
    assert harness.understand_kwargs == [{"model": "claude-sonnet-4-6", "effort": "low"}]


def test_qu_overrides_default_to_none(harness: Harness) -> None:
    harness.run([user("q")])
    assert harness.understand_kwargs == [{"model": None, "effort": None}]


def test_understanding_crash_degrades_to_verbatim(harness: Harness) -> None:
    """understand_query never raises by contract — but if it does, degrade."""
    harness.understand_error = RuntimeError("contract violation")
    result = harness.run([user("responsabilità del custode")])
    assert harness.hybrid_calls[0]["query"] == "responsabilità del custode"
    assert result.query_analysis is None
    assert [h.chunk_id for h in result.hits] == ["hy#0", "hy#1"]


# ---------------------------------------------------------------------------
# Citation following
# ---------------------------------------------------------------------------


def test_citations_appended_last_with_budget_passthrough(harness: Harness) -> None:
    cited = make_hit("dlgs-81-2008#art-14#0", act_ref="dlgs-81-2008", article="14")
    harness.follow_returns = [cited]
    result = harness.run([user("responsabilità del custode")], citation_budget=1234)
    assert result.hits[-1] is cited
    [call] = harness.follow_calls
    assert call["token_budget"] == 1234
    assert call["hits"] == result.hits[:-1]  # followed over the MERGED list


def test_citation_failure_degrades_to_merged_hits(harness: Harness) -> None:
    harness.follow_error = RuntimeError("qdrant scroll exploded")
    result = harness.run([user("responsabilità del custode")])
    assert [h.chunk_id for h in result.hits] == ["hy#0", "hy#1"]


# ---------------------------------------------------------------------------
# Failure policy
# ---------------------------------------------------------------------------


def test_fastpath_failure_degrades_to_hybrid_only(harness: Harness) -> None:
    harness.resolve_error = RuntimeError("scroll failed")
    result = harness.run([user("art. 2051 c.c.")])
    assert [h.chunk_id for h in result.hits] == ["hy#0", "hy#1"]
    assert result.used_fastpath is False


def test_hybrid_failure_propagates(harness: Harness) -> None:
    harness.hybrid_error = RuntimeError("qdrant down")
    with pytest.raises(RuntimeError, match="qdrant down"):
        harness.run([user("responsabilità del custode")])


def test_no_user_message_raises(harness: Harness) -> None:
    with pytest.raises(ValueError):
        harness.run([assistant("ciao")])


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


def test_sources_cover_all_hits_dedup_by_act_and_article(harness: Harness) -> None:
    harness.resolve_article_returns = [
        make_hit("codice-civile#art-2051#0"),
        make_hit("codice-civile#art-2051#1"),  # second chunk, same article
    ]
    harness.hybrid_returns = [
        make_hit("codice-civile#art-2052#0", article="2052"),
        make_hit("codice-civile#art-2051#2"),  # same article again, hybrid side
    ]
    cited = make_hit(
        "dlgs-81-2008#art-14#0",
        act_ref="dlgs-81-2008",
        article="14",
        title="Testo unico sicurezza",
        vigenza="storica",
    )
    harness.follow_returns = [cited]
    result = harness.run([user("art. 2051 c.c.")])
    assert [(s.act_ref, s.article) for s in result.sources] == [
        ("codice-civile", "2051"),
        ("codice-civile", "2052"),
        ("dlgs-81-2008", "14"),
    ]
    followed_source = result.sources[-1]
    assert followed_source.title == "Testo unico sicurezza"
    assert followed_source.vigenza == "storica"
    # completeness: every hit's (act_ref, article) appears in the sources
    assert {(h.act_ref, h.article) for h in result.hits} == {
        (s.act_ref, s.article) for s in result.sources
    }
