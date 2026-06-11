"""Tests for POST /chat (Task F2) — every external call mocked, no network.

The seams are the module attributes ``legger.api.chat.retrieve`` /
``legger.api.chat.stream_answer`` (monkeypatched per test) and
``legger.api.app.get_embedder`` (stubbed so the lifespan never needs a
Voyage key). TestClient drains the StreamingResponse, so assertions parse
the full SSE transcript from ``response.text``.
"""

import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from legger.api import app as app_mod
from legger.api import chat as chat_api
from legger.api.app import create_app
from legger.chat.understanding import QueryAnalysis
from legger.retrieval.pipeline import RetrievalResult, SourceInfo
from legger.retrieval.search import SearchHit
from legger.settings import Settings


def make_hit(
    chunk_id: str = "codice-civile#art-2051#0",
    act_ref: str = "codice-civile",
    article: str = "2051",
    commi: list[str] | None = None,
) -> SearchHit:
    return SearchHit(
        score=0.9,
        chunk_id=chunk_id,
        act_ref=act_ref,
        article=article,
        act_title="Codice civile",
        header="Codice civile\nArt. 2051",
        text="Codice civile\nArt. 2051\n\nCiascuno e' responsabile del danno.",
        vigenza="vigente",
        payload={"commi": commi or []},
    )


def make_result(hits: list[SearchHit] | None = None) -> RetrievalResult:
    hits = hits if hits is not None else [make_hit()]
    seen: set[tuple[str, str]] = set()
    sources = []
    for h in hits:
        if (h.act_ref, h.article) in seen:
            continue
        seen.add((h.act_ref, h.article))
        sources.append(
            SourceInfo(act_ref=h.act_ref, article=h.article, title=h.act_title, vigenza=h.vigenza)
        )
    return RetrievalResult(
        hits=hits,
        sources=sources,
        used_fastpath=True,
        query_analysis=QueryAnalysis(rewritten_query="art. 2051 c.c."),
    )


def parse_sse(body: str) -> list[tuple[str, dict]]:
    """(event, data) tuples from an SSE transcript; comments are dropped."""
    events: list[tuple[str, dict]] = []
    for frame in body.split("\n\n"):
        lines = [ln for ln in frame.split("\n") if ln and not ln.startswith(":")]
        if not lines:
            continue
        event = next(ln.removeprefix("event: ") for ln in lines if ln.startswith("event: "))
        data = next(ln.removeprefix("data: ") for ln in lines if ln.startswith("data: "))
        events.append((event, json.loads(data)))
    return events


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """App with the embedder stubbed at the lifespan seam."""
    monkeypatch.setattr(app_mod, "get_embedder", lambda name: object())
    app = create_app(Settings(qdrant_collection="norme_test", anthropic_api_key="test-key"))
    with TestClient(app) as test_client:  # `with` runs the lifespan
        yield test_client


def stub_calls(
    monkeypatch: pytest.MonkeyPatch,
    *,
    result: RetrievalResult | None = None,
    deltas: list[str] | None = None,
    stop_reason: str = "end_turn",
) -> dict[str, Any]:
    """Patch retrieve + stream_answer on the chat module; record their kwargs."""
    recorded: dict[str, Any] = {}
    result = result if result is not None else make_result()
    deltas = deltas if deltas is not None else ["Risposta."]

    def fake_retrieve(messages: list[dict], **kwargs: Any) -> RetrievalResult:
        recorded["retrieve_messages"] = messages
        recorded["retrieve_kwargs"] = kwargs
        return result

    def fake_stream_answer(messages: list[dict], hits: list[SearchHit], **kwargs: Any):
        recorded["stream_messages"] = messages
        recorded["stream_hits"] = hits
        recorded["stream_kwargs"] = kwargs
        yield from deltas
        return stop_reason

    monkeypatch.setattr(chat_api, "retrieve", fake_retrieve)
    monkeypatch.setattr(chat_api, "stream_answer", fake_stream_answer)
    return recorded


BODY = {"messages": [{"role": "user", "content": "art. 2051 c.c."}]}

#: The effective config reported on `done` when the request carries none.
DEFAULT_CONFIG = {
    "answer_model": "claude-sonnet-4-6",
    "answer_effort": None,
    "qu_model": "claude-haiku-4-5",
    "qu_effort": None,
}


# --- happy path: event sequence ------------------------------------------------


def test_event_sequence_and_payloads(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    recorded = stub_calls(
        monkeypatch,
        deltas=["La custodia: ", "[[codice-civile|art.2051]]", " risponde il custode."],
    )
    response = client.post("/chat", json=BODY)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    events = parse_sse(response.text)
    names = [name for name, _ in events]
    assert names == ["status", "sources", "token", "token", "citation", "token", "done"]

    assert events[0][1] == {"stage": "searching"}
    sources = events[1][1]["sources"]
    assert sources == [
        {
            "act_ref": "codice-civile",
            "article": "2051",
            "title": "Codice civile",
            "vigenza": "vigente",
            "anchor": "art-2051",
        }
    ]
    # Tokens reconstruct the full transcript, marker included.
    tokens = "".join(data["text"] for name, data in events if name == "token")
    assert tokens == "La custodia: [[codice-civile|art.2051]] risponde il custode."
    assert events[-1][1] == {
        "stop_reason": "end_turn",
        "truncated": False,
        "config": DEFAULT_CONFIG,
    }

    # The pipeline got the validated conversation and the app.state wiring.
    assert recorded["retrieve_messages"] == BODY["messages"]
    assert recorded["retrieve_kwargs"]["collection"] == "norme_test"
    assert recorded["stream_hits"] == make_result().hits


def test_citation_verified_true_with_hit_metadata(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub_calls(
        monkeypatch,
        result=make_result([make_hit(commi=["1"])]),
        deltas=["[[codice-civile|art.2051|c.1]]"],
    )
    events = parse_sse(client.post("/chat", json=BODY).text)
    citation = next(data for name, data in events if name == "citation")
    assert citation == {
        "marker": "[[codice-civile|art.2051|c.1]]",
        "act_ref": "codice-civile",
        "article": "2051",
        "comma": "1",
        "title": "Codice civile",
        "vigenza": "vigente",
        "verified": True,
        "reason": "ok",
    }


def test_citation_verified_false_when_not_retrieved(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The model cites an act/article that is NOT among the retrieval hits.
    stub_calls(monkeypatch, deltas=["[[codice-penale|art.52]]"])
    events = parse_sse(client.post("/chat", json=BODY).text)
    citation = next(data for name, data in events if name == "citation")
    assert citation["verified"] is False
    assert citation["reason"] == "act_not_in_context"
    assert citation["title"] is None
    assert citation["vigenza"] is None
    assert citation["act_ref"] == "codice-penale"


def test_citation_comma_not_in_context_is_advisory(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Right act+article but a comma the hits' commi lists do not confirm:
    # verified stays True (comma granularity is advisory, see F3 guardrail),
    # reason carries the nuance, and enrichment still uses the article match.
    stub_calls(
        monkeypatch,
        result=make_result([make_hit(commi=["1", "2"])]),
        deltas=["[[codice-civile|art.2051|c.7]]"],
    )
    events = parse_sse(client.post("/chat", json=BODY).text)
    citation = next(data for name, data in events if name == "citation")
    assert citation["verified"] is True
    assert citation["reason"] == "comma_not_in_context"
    assert citation["title"] == "Codice civile"
    assert citation["vigenza"] == "vigente"


def test_marker_split_across_deltas_is_one_token_and_one_citation(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub_calls(monkeypatch, deltas=["Vedi [[codice-", "civile|art.20", "51]]."])
    events = parse_sse(client.post("/chat", json=BODY).text)
    marker_tokens = [
        data["text"] for name, data in events if name == "token" and "[[" in data["text"]
    ]
    assert marker_tokens == ["[[codice-civile|art.2051]]"]
    assert sum(1 for name, _ in events if name == "citation") == 1


def test_unparseable_bracket_pair_gets_no_citation(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub_calls(monkeypatch, deltas=["testo [[non un marker]] fine"])
    events = parse_sse(client.post("/chat", json=BODY).text)
    assert not any(name == "citation" for name, _ in events)
    tokens = "".join(data["text"] for name, data in events if name == "token")
    assert tokens == "testo [[non un marker]] fine"


def test_done_reports_truncation(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    stub_calls(monkeypatch, deltas=["Risposta tronca"], stop_reason="max_tokens")
    events = parse_sse(client.post("/chat", json=BODY).text)
    assert events[-1] == (
        "done",
        {"stop_reason": "max_tokens", "truncated": True, "config": DEFAULT_CONFIG},
    )


def test_unterminated_marker_flushed_at_end(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub_calls(monkeypatch, deltas=["coda [[codice-civile|art.1"])
    events = parse_sse(client.post("/chat", json=BODY).text)
    tokens = "".join(data["text"] for name, data in events if name == "token")
    assert tokens == "coda [[codice-civile|art.1"
    assert events[-1][0] == "done"


# --- sources anchors ------------------------------------------------------------


def test_source_anchor_uses_chunk_id_occurrence(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A duplicated article number carries the .occurrence disambiguator in
    # the chunk id; the anchor must keep it (matching GET /acts anchors).
    hit = make_hit(chunk_id="dlgs-1-2018#art-1.2#0", act_ref="dlgs-1-2018", article="1")
    stub_calls(monkeypatch, result=make_result([hit]))
    events = parse_sse(client.post("/chat", json=BODY).text)
    sources = next(data for name, data in events if name == "sources")["sources"]
    assert sources[0]["anchor"] == "art-1-2"


# --- failure modes ---------------------------------------------------------------


def test_retrieve_failure_yields_error_event(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(messages: list[dict], **kwargs: Any) -> RetrievalResult:
        raise RuntimeError("qdrant down (internal detail)")

    monkeypatch.setattr(chat_api, "retrieve", boom)
    response = client.post("/chat", json=BODY)
    assert response.status_code == 200  # error travels inside the stream
    events = parse_sse(response.text)
    assert [name for name, _ in events] == ["status", "error"]
    message = events[-1][1]["message"]
    assert "qdrant" not in message  # no internals leaked
    assert "errore" in message.lower()


def test_generation_failure_midstream_yields_error_event(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorded = stub_calls(monkeypatch)

    def exploding_stream(messages: list[dict], hits: list[SearchHit], **kwargs: Any):
        yield "inizio "
        raise RuntimeError("anthropic APIError (internal detail)")

    monkeypatch.setattr(chat_api, "stream_answer", exploding_stream)
    events = parse_sse(client.post("/chat", json=BODY).text)
    names = [name for name, _ in events]
    assert names == ["status", "sources", "token", "error"]
    assert "done" not in names
    assert "anthropic" not in events[-1][1]["message"].lower()
    assert recorded  # retrieve ran


# --- validation (422) -------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {"messages": []},  # empty conversation
        {"messages": [{"role": "user", "content": ""}]},  # empty content
        {"messages": [{"role": "assistant", "content": "ciao"}]},  # last not user
        {
            "messages": [
                {"role": "user", "content": "a"},
                {"role": "assistant", "content": "b"},
            ]
        },  # last not user (multi-turn)
        {"messages": [{"role": "system", "content": "x"}]},  # bad role
        {"messages": [{"role": "user", "content": "x" * 8001}]},  # content too long
        {"messages": [{"role": "user", "content": "x"}] * 21},  # too many turns
        {},  # no messages key
    ],
    ids=[
        "empty-list",
        "empty-content",
        "last-is-assistant",
        "last-is-assistant-multiturn",
        "bad-role",
        "content-too-long",
        "too-many-turns",
        "missing-key",
    ],
)
def test_validation_422(client: TestClient, monkeypatch: pytest.MonkeyPatch, body: dict) -> None:
    stub_calls(monkeypatch)  # must never be reached
    response = client.post("/chat", json=body)
    assert response.status_code == 422


def test_twenty_turns_is_accepted(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    stub_calls(monkeypatch)
    turns = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}] * 9
    turns += [{"role": "user", "content": "a"}, {"role": "user", "content": "fine"}]
    assert len(turns) == 20
    response = client.post("/chat", json={"messages": turns})
    assert response.status_code == 200


# --- beta-testing config (per-conversation model/effort) -------------------------


def test_config_plumbed_to_retrieve_and_stream_answer(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorded = stub_calls(monkeypatch)
    body = {
        **BODY,
        "config": {
            "answer_model": "claude-opus-4-8",
            "answer_effort": "max",
            "qu_model": "claude-sonnet-4-6",
            "qu_effort": "low",
        },
    }
    events = parse_sse(client.post("/chat", json=body).text)
    assert recorded["retrieve_kwargs"]["qu_model"] == "claude-sonnet-4-6"
    assert recorded["retrieve_kwargs"]["qu_effort"] == "low"
    assert recorded["stream_kwargs"]["model"] == "claude-opus-4-8"
    assert recorded["stream_kwargs"]["effort"] == "max"
    # done reports the effective config (transparency for testers).
    assert events[-1][1]["config"] == {
        "answer_model": "claude-opus-4-8",
        "answer_effort": "max",
        "qu_model": "claude-sonnet-4-6",
        "qu_effort": "low",
    }


def test_partial_config_fills_defaults_on_done(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorded = stub_calls(monkeypatch)
    body = {**BODY, "config": {"answer_model": "claude-haiku-4-5", "answer_effort": "high"}}
    events = parse_sse(client.post("/chat", json=body).text)
    # haiku-4-5 does not support effort: the effective config reports None
    # (build_model_kwargs drops it before the API call).
    assert events[-1][1]["config"] == {
        "answer_model": "claude-haiku-4-5",
        "answer_effort": None,
        "qu_model": "claude-haiku-4-5",
        "qu_effort": None,
    }
    assert recorded["retrieve_kwargs"]["qu_model"] is None


@pytest.mark.parametrize(
    "config",
    [
        {"answer_model": "claude-fantasia-9"},  # unknown model
        {"answer_model": "claude-sonnet-4-6-20251114"},  # date-suffixed variant
        {"qu_model": "claude-opus-4-8"},  # valid answer model, NOT allowed for QU
        {"answer_effort": "xhigh"},  # effort outside the allowlist
        {"qu_effort": "massimo"},  # effort outside the allowlist
    ],
    ids=["unknown-model", "date-suffixed", "qu-not-allowed", "bad-answer-effort", "bad-qu-effort"],
)
def test_config_validation_422(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, config: dict
) -> None:
    stub_calls(monkeypatch)  # must never be reached
    response = client.post("/chat", json={**BODY, "config": config})
    assert response.status_code == 422


def test_null_and_missing_config_are_accepted(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub_calls(monkeypatch)
    assert client.post("/chat", json={**BODY, "config": None}).status_code == 200
    assert client.post("/chat", json={**BODY, "config": {}}).status_code == 200


# --- GET /chat/models -------------------------------------------------------------


def test_chat_models_catalog_shape(client: TestClient) -> None:
    response = client.get("/chat/models")
    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"answer", "qu", "effort_levels"}
    assert payload["effort_levels"] == ["low", "medium", "high", "max"]
    assert payload["answer"]["default"] == "claude-sonnet-4-6"
    assert payload["qu"]["default"] == "claude-haiku-4-5"
    answer_ids = {m["id"] for m in payload["answer"]["models"]}
    assert answer_ids == {
        "claude-haiku-4-5",
        "claude-sonnet-4-6",
        "claude-opus-4-6",
        "claude-opus-4-8",
    }
    assert {m["id"] for m in payload["qu"]["models"]} == {"claude-haiku-4-5", "claude-sonnet-4-6"}
    sonnet = next(m for m in payload["answer"]["models"] if m["id"] == "claude-sonnet-4-6")
    assert sonnet == {
        "id": "claude-sonnet-4-6",
        "label": "Sonnet 4.6",
        "input_usd_mtok": 3.0,
        "output_usd_mtok": 15.0,
        "supports_effort": True,
    }
    haiku = next(m for m in payload["qu"]["models"] if m["id"] == "claude-haiku-4-5")
    assert haiku["supports_effort"] is False


# --- generator teardown --------------------------------------------------------


def test_generation_closed_deterministically(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stream_answer generator's cleanup runs by end-of-response.

    chat.py wraps the generation loop in try/finally with generation.close(),
    so the Anthropic stream teardown is deterministic (also on client
    disconnect) instead of waiting for garbage collection.
    """
    closed: list[bool] = []

    def tracked_stream(messages: list[dict], hits: list[SearchHit], **kwargs: Any):
        try:
            yield "Risposta."
        finally:
            closed.append(True)
        return "end_turn"

    stub_calls(monkeypatch)
    monkeypatch.setattr(chat_api, "stream_answer", tracked_stream)
    response = client.post("/chat", json=BODY)
    assert closed == [True]
    assert parse_sse(response.text)[-1][0] == "done"
