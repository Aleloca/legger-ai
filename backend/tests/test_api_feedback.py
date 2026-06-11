"""Tests for POST /feedback: insert seam mocked (no DB), plus one -m db run.

Same lifespan seam as the other API tests (``legger.api.app.get_embedder``
stubbed); the insert seam is the module attribute
``legger.api.feedback.insert_feedback``, monkeypatched per test — the SQL
itself is covered by the ``insert_feedback`` tests in test_db.py.
"""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from legger.api import app as app_mod
from legger.api import feedback as feedback_api
from legger.api.app import create_app
from legger.settings import Settings


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(app_mod, "get_embedder", lambda name: object())
    app = create_app(Settings(anthropic_api_key="test-key"))
    with TestClient(app) as test_client:  # `with` runs the lifespan
        yield test_client


def stub_insert(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patch the insert seam; return the dict that records its kwargs."""
    recorded: dict[str, Any] = {}

    def fake_insert(bind: Any, **kwargs: Any) -> None:
        recorded["bind"] = bind
        recorded.update(kwargs)

    monkeypatch.setattr(feedback_api, "insert_feedback", fake_insert)
    return recorded


CITATION = {
    "marker": "[[codice-civile|art.2051]]",
    "act_ref": "codice-civile",
    "article": "2051",
    "comma": None,
    "verified": True,
}

CONFIG = {
    "answer_model": "claude-sonnet-4-6",
    "answer_effort": "high",
    "qu_model": "claude-haiku-4-5",
    "qu_effort": None,
}

BODY = {
    "rating": -1,
    "reason": "Cita l'articolo sbagliato.",
    "question": "art. 2051 c.c.",
    "answer": "Risposta con [[codice-civile|art.2051]].",
    "citations": [CITATION],
    "config": CONFIG,
}


# --- happy path -------------------------------------------------------------------


def test_full_feedback_inserted(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    recorded = stub_insert(monkeypatch)
    response = client.post("/feedback", json=BODY)
    assert response.status_code == 204
    assert response.content == b""
    assert recorded["rating"] == -1
    assert recorded["reason"] == "Cita l'articolo sbagliato."
    assert recorded["question"] == "art. 2051 c.c."
    assert recorded["answer"] == "Risposta con [[codice-civile|art.2051]]."
    assert recorded["citations"] == [CITATION]
    assert recorded["config"] == CONFIG
    assert recorded["bind"] is client.app.state.engine


def test_minimal_thumbs_up(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    recorded = stub_insert(monkeypatch)
    body = {"rating": 1, "question": "domanda", "answer": "risposta"}
    assert client.post("/feedback", json=body).status_code == 204
    assert recorded["rating"] == 1
    assert recorded["reason"] is None
    assert recorded["citations"] == []
    # Missing config normalizes to the all-None effective shape.
    assert recorded["config"] == {
        "answer_model": None,
        "answer_effort": None,
        "qu_model": None,
        "qu_effort": None,
    }


def test_citation_extra_keys_dropped_not_rejected(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The frontend's Citation carries title/vigenza/reason too: the stored
    # shape keeps only the lean fields (see module docstring of feedback.py).
    recorded = stub_insert(monkeypatch)
    fat_citation = {**CITATION, "title": "Codice civile", "vigenza": "vigente", "reason": "ok"}
    body = {**BODY, "citations": [fat_citation]}
    assert client.post("/feedback", json=body).status_code == 204
    assert recorded["citations"] == [CITATION]


# --- validation (422) -------------------------------------------------------------


@pytest.mark.parametrize(
    "patch",
    [
        {"rating": 0},  # rating outside {-1, 1}
        {"rating": "su"},  # rating not an int
        {"rating": None},  # rating required
        {"reason": "x" * 2001},  # reason over the cap
        {"question": ""},  # empty question
        {"question": "x" * 8001},  # question over the cap
        {"answer": ""},  # empty answer
        {"answer": "x" * 32001},  # answer over the cap
        {"citations": [CITATION] * 101},  # too many citations
        {"citations": [{"marker": "[[x]]"}]},  # citation missing required keys
        {"config": {"answer_model": "claude-fantasia-9"}},  # unknown model
        {"config": {"qu_model": "claude-opus-4-8"}},  # not allowed for QU
        {"config": {"answer_effort": "xhigh"}},  # effort outside the allowlist
        {"config": {**CONFIG, "temperature": 0.7}},  # unknown config key
        {"config": "sonnet"},  # config not an object
    ],
    ids=[
        "rating-zero",
        "rating-string",
        "rating-missing",
        "reason-too-long",
        "question-empty",
        "question-too-long",
        "answer-empty",
        "answer-too-long",
        "too-many-citations",
        "citation-missing-keys",
        "unknown-model",
        "qu-not-allowed",
        "bad-effort",
        "unknown-config-key",
        "config-not-object",
    ],
)
def test_validation_422(client: TestClient, monkeypatch: pytest.MonkeyPatch, patch: dict) -> None:
    recorded = stub_insert(monkeypatch)  # must never be reached
    body = {**BODY, **patch}
    if patch.get("rating") is None and "rating" in patch:
        body.pop("rating")
    assert client.post("/feedback", json=body).status_code == 422
    assert "rating" not in recorded


def test_boundary_sizes_accepted(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    stub_insert(monkeypatch)
    body = {
        "rating": -1,
        "reason": "x" * 2000,
        "question": "x" * 8000,
        "answer": "x" * 32000,
        "citations": [CITATION] * 100,
    }
    assert client.post("/feedback", json=body).status_code == 204


# --- db down (503) ----------------------------------------------------------------


def test_db_down_503_user_safe_italian(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(bind: Any, **kwargs: Any) -> None:
        raise OperationalError("INSERT", {}, Exception("connection refused (internal)"))

    monkeypatch.setattr(feedback_api, "insert_feedback", boom)
    response = client.post("/feedback", json=BODY)
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert "connection" not in detail  # no internals leaked
    assert "feedback" in detail.lower()


# --- integration: real Postgres (-m db) --------------------------------------------


@pytest.mark.db
def test_feedback_row_lands_in_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    from legger.db import message_feedback

    monkeypatch.setattr(app_mod, "get_embedder", lambda name: object())
    app = create_app(Settings(anthropic_api_key="test-key"))
    question = "test:api:feedback domanda?"
    with TestClient(app) as client:
        response = client.post("/feedback", json={**BODY, "question": question})
        assert response.status_code == 204
        engine = client.app.state.engine
        with engine.begin() as conn:
            row = conn.execute(
                select(message_feedback).where(message_feedback.c.question == question)
            ).one()
            assert row.rating == -1
            assert row.citations == [CITATION]
            assert row.config == CONFIG
            conn.execute(message_feedback.delete().where(message_feedback.c.question == question))
