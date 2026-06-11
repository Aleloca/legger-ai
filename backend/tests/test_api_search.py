"""Tests for GET /search (Task F4) — retrieval calls mocked, no network.

The seams are the module attributes ``legger.api.search.resolve_refs`` /
``legger.api.search.hybrid_search`` (monkeypatched per test) and
``legger.api.app.get_embedder`` (stubbed so the lifespan never needs a
Voyage key). ``extract_refs`` is pure (no I/O) and runs for real, so the
exact-vs-semantic routing is exercised through actual query text.
"""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from legger.api import app as app_mod
from legger.api import search as search_api
from legger.api.app import create_app
from legger.retrieval.fastpath import ExtractedRef
from legger.retrieval.search import SearchHit
from legger.settings import Settings

HEADER = "Codice civile\nArt. 2051 - Danno cagionato da cose in custodia"
BODY = "Ciascuno è responsabile del danno cagionato dalle cose che ha in custodia."


def make_hit(
    chunk_id: str = "codice-civile#art-2051#0",
    act_ref: str = "codice-civile",
    article: str = "2051",
    score: float = 1.0,
    header: str = HEADER,
    body: str = BODY,
) -> SearchHit:
    return SearchHit(
        score=score,
        chunk_id=chunk_id,
        act_ref=act_ref,
        article=article,
        act_title="Codice civile",
        header=header,
        text=f"{header}\n\n{body}" if body else header,
        vigenza="vigente",
        payload={},
    )


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """App with the embedder stubbed at the lifespan seam."""
    monkeypatch.setattr(app_mod, "get_embedder", lambda name: object())
    app = create_app(Settings(qdrant_collection="norme_test", anthropic_api_key="test-key"))
    with TestClient(app) as test_client:  # `with` runs the lifespan
        yield test_client


def stub_tiers(
    monkeypatch: pytest.MonkeyPatch,
    *,
    exact: list[SearchHit] | None = None,
    semantic: list[SearchHit] | None = None,
) -> dict[str, Any]:
    """Patch resolve_refs + hybrid_search on the search module; record calls."""
    recorded: dict[str, Any] = {}

    def fake_resolve(refs: list[ExtractedRef], **kwargs: Any) -> list[SearchHit]:
        recorded["resolve_refs"] = refs
        recorded["resolve_kwargs"] = kwargs
        return list(exact or [])

    def fake_hybrid(query: str, **kwargs: Any) -> list[SearchHit]:
        recorded["hybrid_query"] = query
        recorded["hybrid_kwargs"] = kwargs
        return list(semantic or [])

    monkeypatch.setattr(search_api, "resolve_refs", fake_resolve)
    monkeypatch.setattr(search_api, "hybrid_search", fake_hybrid)
    return recorded


# --- exact tier -----------------------------------------------------------------


def test_exact_path(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    recorded = stub_tiers(monkeypatch, exact=[make_hit()])
    response = client.get("/search", params={"q": "art. 2051 c.c.", "k": 1})
    assert response.status_code == 200
    results = response.json()["results"]
    assert results == [
        {
            "act_ref": "codice-civile",
            "article": "2051",
            "act_title": "Codice civile",
            "snippet": BODY,
            "vigenza": "vigente",
            "anchor": "art-2051",
            "match": "exact",
        }
    ]
    # The real extract_refs produced the article-level ref that was resolved.
    assert recorded["resolve_refs"] == [
        ExtractedRef(act_ref="codice-civile", act_type="codice", article="2051")
    ]
    assert recorded["resolve_kwargs"]["collection"] == "norme_test"
    # k filled by the exact tier: the semantic tier never ran.
    assert "hybrid_query" not in recorded


def test_act_level_ref_goes_semantic(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    # An act named WITHOUT an article ("codice civile") is not an exact
    # article match: resolve_refs is skipped, the semantic tier answers.
    recorded = stub_tiers(monkeypatch, semantic=[make_hit(score=0.7)])
    response = client.get("/search", params={"q": "codice civile"})
    assert response.status_code == 200
    assert [r["match"] for r in response.json()["results"]] == ["semantic"]
    assert "resolve_refs" not in recorded


# --- semantic tier ----------------------------------------------------------------


def test_semantic_path(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    hits = [
        make_hit(score=0.9),
        make_hit(chunk_id="codice-civile#art-2052#0", article="2052", score=0.8),
    ]
    recorded = stub_tiers(monkeypatch, semantic=hits)
    response = client.get("/search", params={"q": "responsabilità del custode"})
    assert response.status_code == 200
    results = response.json()["results"]
    assert [r["match"] for r in results] == ["semantic", "semantic"]
    assert [r["article"] for r in results] == ["2051", "2052"]
    # No refs in the query: the exact tier never ran.
    assert "resolve_refs" not in recorded
    assert recorded["hybrid_query"] == "responsabilità del custode"
    assert recorded["hybrid_kwargs"]["collection"] == "norme_test"
    assert recorded["hybrid_kwargs"]["k"] == 10  # default


def test_mixed_fill_to_k_exact_first(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    exact = [
        make_hit(),
        make_hit(chunk_id="codice-civile#art-2051#1"),
    ]
    semantic = [
        make_hit(chunk_id="codice-civile#art-2052#0", article="2052", score=0.9),
        make_hit(chunk_id="codice-civile#art-2053#0", article="2053", score=0.8),
        make_hit(chunk_id="codice-civile#art-2054#0", article="2054", score=0.7),
    ]
    stub_tiers(monkeypatch, exact=exact, semantic=semantic)
    response = client.get("/search", params={"q": "art. 2051 c.c. custodia", "k": 3})
    results = response.json()["results"]
    # Exact first, semantic fills the remainder, capped at k=3.
    assert [r["match"] for r in results] == ["exact", "exact", "semantic"]
    assert results[2]["article"] == "2052"


def test_dedup_by_chunk_id(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    # The semantic tier also finds the chunk the exact tier already returned:
    # it must appear once, as "exact".
    semantic = [
        make_hit(score=0.9),  # same chunk_id as the exact hit
        make_hit(chunk_id="codice-civile#art-2052#0", article="2052", score=0.8),
    ]
    stub_tiers(monkeypatch, exact=[make_hit()], semantic=semantic)
    response = client.get("/search", params={"q": "art. 2051 c.c.", "k": 5})
    results = response.json()["results"]
    assert [(r["article"], r["match"]) for r in results] == [
        ("2051", "exact"),
        ("2052", "semantic"),
    ]


# --- snippet ----------------------------------------------------------------------


def test_snippet_strips_header_and_cuts_at_word_boundary(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    long_body = " ".join(f"parola{i}" for i in range(60))  # ~540 chars of words
    stub_tiers(monkeypatch, semantic=[make_hit(body=long_body)])
    response = client.get("/search", params={"q": "responsabilità"})
    snippet = response.json()["results"][0]["snippet"]
    assert "Codice civile" not in snippet  # header stripped
    assert snippet.endswith("…")
    core = snippet[:-1]
    assert len(core) <= search_api.SNIPPET_CHARS
    assert long_body.startswith(core)
    assert long_body[len(core)] == " "  # cut exactly at a word boundary


def test_short_body_snippet_is_whole_and_unellipsized(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub_tiers(monkeypatch, semantic=[make_hit()])
    response = client.get("/search", params={"q": "custodia"})
    assert response.json()["results"][0]["snippet"] == BODY


# --- anchor -----------------------------------------------------------------------


def test_anchor_keeps_chunk_id_occurrence(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A duplicated article number carries the .occurrence disambiguator in
    # the chunk id; the anchor must keep it (matching GET /acts anchors).
    hit = make_hit(chunk_id="dlgs-1-2018#art-1.2#0", act_ref="dlgs-1-2018", article="1")
    stub_tiers(monkeypatch, semantic=[hit])
    response = client.get("/search", params={"q": "protezione civile"})
    assert response.json()["results"][0]["anchor"] == "art-1-2"


# --- validation (422) ---------------------------------------------------------------


@pytest.mark.parametrize(
    "params",
    [
        {},  # q missing
        {"q": ""},  # q empty
        {"q": "   "},  # q whitespace-only
        {"q": "x" * 501},  # q too long
        {"q": "custodia", "k": 0},  # k below range
        {"q": "custodia", "k": 26},  # k above range
        {"q": "custodia", "k": "molti"},  # k not an int
    ],
    ids=["missing-q", "empty-q", "blank-q", "q-too-long", "k-zero", "k-over", "k-not-int"],
)
def test_validation_422(client: TestClient, monkeypatch: pytest.MonkeyPatch, params: dict) -> None:
    recorded = stub_tiers(monkeypatch)  # must never be reached
    response = client.get("/search", params=params)
    assert response.status_code == 422
    assert not recorded


# --- failure modes (502) -------------------------------------------------------------


def test_502_on_qdrant_failure_in_exact_tier(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(refs: list[ExtractedRef], **kwargs: Any) -> list[SearchHit]:
        raise RuntimeError("qdrant connection refused (internal detail)")

    monkeypatch.setattr(search_api, "resolve_refs", boom)
    response = client.get("/search", params={"q": "art. 2051 c.c."})
    assert response.status_code == 502
    detail = response.json()["detail"]
    assert "qdrant" not in detail.lower()  # no internals leaked
    assert "ricerca" in detail.lower()  # user-safe Italian


def test_502_on_qdrant_failure_in_semantic_tier(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(query: str, **kwargs: Any) -> list[SearchHit]:
        raise RuntimeError("qdrant timeout (internal detail)")

    monkeypatch.setattr(search_api, "hybrid_search", boom)
    response = client.get("/search", params={"q": "responsabilità del custode"})
    assert response.status_code == 502
    assert "qdrant" not in response.json()["detail"].lower()


# --- embedder degrade ------------------------------------------------------------


def test_no_embedder_exact_results_still_served(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub_tiers(monkeypatch, exact=[make_hit()])
    client.app.state.embedder = None
    response = client.get("/search", params={"q": "art. 2051 c.c."})
    assert response.status_code == 200
    assert [r["match"] for r in response.json()["results"]] == ["exact"]


def test_no_embedder_and_no_exact_is_502(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub_tiers(monkeypatch)
    client.app.state.embedder = None
    response = client.get("/search", params={"q": "responsabilità del custode"})
    assert response.status_code == 502
