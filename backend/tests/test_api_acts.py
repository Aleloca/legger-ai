"""Tests for the F1 API: create_app + GET /acts/{act_ref}.

TestClient against an app built on the FIXTURE corpus
(``tests/fixtures/corpus``), never the real one. The Postgres lookup is
stubbed at the module seam (:func:`legger.api.acts.lookup_act`) — the
documented testing strategy: the lookup is a trivial PK SELECT, everything
under test here is the endpoint behavior around it (parse, shape, anchors,
the ?article window, the 404/503 split, CORS).
"""

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from legger.api import acts as acts_api
from legger.api.acts import anchor_from_chunk_segment, article_anchor
from legger.api.app import create_app
from legger.settings import Settings

FIXTURE_CORPUS = Path(__file__).parent / "fixtures" / "corpus"

#: A real Codici fixture (51 articles, incl. a 46-bis): rich enough for the
#: shape/anchor/window assertions without being multi-MB.
FIXTURE_FILE = "Codici/Codice della protezione civile. 18G00011.md"

ACT_ROW: dict[str, Any] = {
    "act_ref": "dlgs-1-2018",
    "act_type": "dlgs",
    "title": "DECRETO LEGISLATIVO 02 gennaio 2018 n. 1",
    "collection": "Codici",
    "vigenza": "vigente",
    "file_path": FIXTURE_FILE,
}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """App on the fixture corpus, with the DB lookup stubbed in-memory."""

    def fake_lookup(engine: Any, act_ref: str) -> dict | None:
        return dict(ACT_ROW) if act_ref == ACT_ROW["act_ref"] else None

    monkeypatch.setattr(acts_api, "lookup_act", fake_lookup)
    acts_api._parse_cached.cache_clear()
    app = create_app(Settings(corpus_path=FIXTURE_CORPUS))
    with TestClient(app) as test_client:  # `with` runs the lifespan
        yield test_client


# --- 200: response shape ------------------------------------------------------


def test_get_act_shape(client: TestClient) -> None:
    response = client.get("/acts/dlgs-1-2018")
    assert response.status_code == 200
    body = response.json()
    # Identity comes from the acts row, not from the parse.
    assert body["act_ref"] == "dlgs-1-2018"
    assert body["title"] == "DECRETO LEGISLATIVO 02 gennaio 2018 n. 1"
    assert body["act_type"] == "dlgs"
    assert body["vigenza"] == "vigente"
    assert body["collection"] == "Codici"
    # Articles come from parsing the fixture file.
    assert len(body["articles"]) == 51
    first = body["articles"][0]
    assert set(first) == {"number", "heading", "path", "commi", "anchor"}
    assert first["number"] == "1"
    assert first["heading"].startswith("Definizione e finalita'")
    assert first["path"] == [
        "CAPO I - Capo I Finalita', attivita' e composizione "
        "del Servizio nazionale della protezione civile"
    ]
    assert first["commi"][0]["number"] == "1"
    assert "protezione civile" in first["commi"][0]["text"]


# --- anchors ------------------------------------------------------------------


def test_anchor_format(client: TestClient) -> None:
    body = client.get("/acts/dlgs-1-2018").json()
    anchors = {a["number"]: a["anchor"] for a in body["articles"]}
    assert anchors["1"] == "art-1"
    assert anchors["46-bis"] == "art-46-bis"  # suffix kept, already URL-safe
    # All anchors are URL-safe slugs and unique within the act.
    all_anchors = [a["anchor"] for a in body["articles"]]
    assert all(re.fullmatch(r"art-[a-z0-9-]+", a) for a in all_anchors)
    assert len(all_anchors) == len(set(all_anchors))


def test_article_anchor_function() -> None:
    assert article_anchor("18") == "art-18"
    assert article_anchor("613-bis") == "art-613-bis"
    assert article_anchor("UNICO") == "art-unico"
    # Repeated numbers mirror the chunk-id ".{occ}" disambiguator, slugified.
    assert article_anchor("1", occurrence=2) == "art-1-2"


def test_anchor_from_chunk_segment() -> None:
    """The chunk-id article segment maps onto an anchor mechanically (for F3)."""
    assert anchor_from_chunk_segment("art-18") == "art-18"
    assert anchor_from_chunk_segment("art-613-bis") == "art-613-bis"
    assert anchor_from_chunk_segment("art-1.2") == "art-1-2"
    assert anchor_from_chunk_segment("art-UNICO") == "art-unico"
    # Both sides of the mapping agree on the same article occurrence.
    assert anchor_from_chunk_segment("art-1.2") == article_anchor("1", occurrence=2)


# --- ?article window ----------------------------------------------------------


def test_article_filter_returns_window(client: TestClient) -> None:
    body = client.get("/acts/dlgs-1-2018", params={"article": "2"}).json()
    assert [a["number"] for a in body["articles"]] == ["1", "2", "3"]


def test_article_filter_clamps_at_document_edges(client: TestClient) -> None:
    first = client.get("/acts/dlgs-1-2018", params={"article": "1"}).json()
    assert [a["number"] for a in first["articles"]] == ["1", "2"]
    last = client.get("/acts/dlgs-1-2018", params={"article": "50"}).json()
    assert [a["number"] for a in last["articles"]] == ["49", "50"]


def test_article_filter_unknown_article_404(client: TestClient) -> None:
    response = client.get("/acts/dlgs-1-2018", params={"article": "9999"})
    assert response.status_code == 404
    assert "9999" in response.json()["detail"]


# --- error paths --------------------------------------------------------------


def test_unknown_act_ref_404(client: TestClient) -> None:
    response = client.get("/acts/no-such-act")
    assert response.status_code == 404
    assert response.json() == {"detail": "Atto 'no-such-act' non trovato."}


def test_row_exists_but_file_missing_503(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DB/corpus skew (row present, file gone) is a 503, not a 404."""
    row = dict(ACT_ROW, file_path="Codici/sparito-durante-un-update.md")
    monkeypatch.setattr(acts_api, "lookup_act", lambda engine, act_ref: dict(row))
    response = client.get("/acts/dlgs-1-2018")
    assert response.status_code == 503
    detail = response.json()["detail"]
    # Generic message: the on-disk path is logged server-side, never leaked.
    assert "sparito-durante-un-update.md" not in detail
    assert "non è al momento" in detail


def test_file_vanishes_between_stat_and_parse_503(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TOCTOU: stat succeeds, then the parse hits a vanished file -> still 503."""

    def vanished(file_path: str, mtime_ns: int) -> Any:
        raise FileNotFoundError(file_path)

    monkeypatch.setattr(acts_api, "_parse_cached", vanished)
    response = client.get("/acts/dlgs-1-2018")
    assert response.status_code == 503
    assert FIXTURE_FILE not in response.json()["detail"]


# --- app wiring ---------------------------------------------------------------


def test_cors_header_for_nextjs_dev_origin(client: TestClient) -> None:
    response = client.get(
        "/acts/dlgs-1-2018", headers={"Origin": "http://localhost:3000"}
    )
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_lifespan_exposes_shared_state(client: TestClient) -> None:
    state = client.app.state
    assert state.settings.corpus_path == FIXTURE_CORPUS
    assert state.engine is not None
    assert state.qdrant is not None


def test_parse_cache_hit_on_second_request(client: TestClient) -> None:
    """Same (path, mtime) -> the second request is served from the LRU."""
    client.get("/acts/dlgs-1-2018")
    before = acts_api._parse_cached.cache_info()
    client.get("/acts/dlgs-1-2018")
    after = acts_api._parse_cached.cache_info()
    assert after.hits == before.hits + 1
