"""Unit tests for legger.chat (prompts + generate) — mocked Anthropic client,
mocked retrieval, no network.

Pins down: context formatting (header dedup, one block per hit), the marker
instruction in the system prompt, and the message/system assembly sent to the
Anthropic streaming API.
"""

from contextlib import contextmanager
from typing import Any

import pytest

from legger.chat import generate as generate_mod
from legger.chat.generate import (
    MODEL_SONNET,
    chat_once,
    last_user_message,
    retrieve_for_messages,
    stream_answer,
)
from legger.chat.prompts import SYSTEM_PROMPT, format_context
from legger.retrieval.search import SearchHit


def make_hit(
    chunk_id: str = "codice-civile#art-2051#0",
    act_ref: str = "codice-civile",
    article: str = "2051",
    header: str = "Codice civile\nArt. 2051",
    text: str = "Codice civile\nArt. 2051\n\nCiascuno e' responsabile del danno.",
) -> SearchHit:
    return SearchHit(
        score=0.5,
        chunk_id=chunk_id,
        act_ref=act_ref,
        article=article,
        act_title="Codice civile",
        header=header,
        text=text,
        vigenza="vigente",
        payload={},
    )


class FakeStream:
    """Stands in for the context manager returned by messages.stream()."""

    def __init__(self, deltas: list[str]) -> None:
        self.text_stream = iter(deltas)

    def __enter__(self) -> "FakeStream":
        return self

    def __exit__(self, *exc_info: object) -> None:
        pass


class FakeAnthropic:
    """Records the kwargs of every messages.stream() call."""

    def __init__(self, deltas: list[str] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._deltas = deltas if deltas is not None else ["Risposta ", "grounded."]
        outer = self

        class _Messages:
            @contextmanager
            def stream(self, **kwargs: Any):
                outer.calls.append(kwargs)
                yield FakeStream(outer._deltas)

        self.messages = _Messages()


# --- prompts.SYSTEM_PROMPT ---------------------------------------------------


def test_system_prompt_has_marker_instruction() -> None:
    assert "[[act_ref|art.N|c.M]]" in SYSTEM_PROMPT
    assert "[[act_ref|art.N]]" in SYSTEM_PROMPT  # comma-less variant


def test_system_prompt_core_rules_present() -> None:
    # Grounding, refusal, and product-voice rules (Italian).
    assert "SOLO sulla base dei passaggi" in SYSTEM_PROMPT
    assert "MAI inventare" in SYSTEM_PROMPT
    assert "consulenza legale" in SYSTEM_PROMPT


# --- prompts.format_context ---------------------------------------------------


def test_format_context_block_shape_and_header_dedup() -> None:
    hit = make_hit()
    out = format_context([hit])
    assert out == (
        "--- [codice-civile#art-2051#0] Codice civile\nArt. 2051\n"
        "Ciascuno e' responsabile del danno."
    )
    # The header appears once (in the --- line), not repeated in the body.
    assert out.count("Codice civile\nArt. 2051") == 1


def test_format_context_keeps_text_whole_when_header_not_a_prefix() -> None:
    hit = make_hit(header="Altro header", text="Testo senza header davanti.")
    out = format_context([hit])
    assert "--- [codice-civile#art-2051#0] Altro header" in out
    assert "Testo senza header davanti." in out


def test_format_context_multiple_hits_one_block_each() -> None:
    hits = [make_hit(), make_hit(chunk_id="codice-penale#art-52#0", act_ref="codice-penale")]
    out = format_context(hits)
    assert out.count("--- [") == 2
    assert "[codice-penale#art-52#0]" in out


def test_format_context_empty() -> None:
    assert format_context([]) == ""


# --- generate -----------------------------------------------------------------


def test_last_user_message_picks_most_recent_user_turn() -> None:
    messages = [
        {"role": "user", "content": "prima"},
        {"role": "assistant", "content": "risposta"},
        {"role": "user", "content": "seconda"},
    ]
    assert last_user_message(messages) == "seconda"


def test_last_user_message_raises_without_user_turn() -> None:
    with pytest.raises(ValueError):
        last_user_message([{"role": "assistant", "content": "x"}])


def test_retrieve_for_messages_queries_last_user_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_search(query: str, **kwargs: Any) -> list[SearchHit]:
        calls.append((query, kwargs))
        return [make_hit()]

    monkeypatch.setattr(generate_mod, "hybrid_search", fake_search)
    messages = [
        {"role": "user", "content": "art. 2051 c.c."},
        {"role": "assistant", "content": "..."},
        {"role": "user", "content": "e la prova liberatoria?"},
    ]
    hits = retrieve_for_messages(
        messages, collection="norme_test", embedder="emb", client="qdrant", k=7
    )
    assert hits == [make_hit()]
    assert calls == [
        (
            "e la prova liberatoria?",
            {"collection": "norme_test", "embedder": "emb", "client": "qdrant", "k": 7},
        )
    ]


def test_stream_answer_message_assembly() -> None:
    fake = FakeAnthropic()
    messages = [{"role": "user", "content": "art. 2051 c.c."}]
    hits = [make_hit()]

    deltas = list(stream_answer(messages, hits, anthropic_client=fake))

    assert deltas == ["Risposta ", "grounded."]
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["model"] == MODEL_SONNET
    assert call["max_tokens"] == generate_mod.MAX_TOKENS
    assert call["temperature"] == generate_mod.TEMPERATURE
    # Conversation passed through untouched, after the system blocks.
    assert call["messages"] == messages

    system = call["system"]
    assert system[0]["text"] == SYSTEM_PROMPT
    assert system[0]["cache_control"] == {"type": "ephemeral"}
    # Context block: formatted hits inside <contesto>, after the stable prompt.
    assert "<contesto>" in system[1]["text"]
    assert format_context(hits) in system[1]["text"]


def test_chat_once_composes_retrieval_and_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hit = make_hit()
    monkeypatch.setattr(generate_mod, "hybrid_search", lambda query, **kwargs: [hit])
    fake = FakeAnthropic(deltas=["a", "b"])

    out = list(
        chat_once(
            [{"role": "user", "content": "difesa legittima"}],
            collection="norme_test",
            embedder="emb",
            client="qdrant",
            anthropic_client=fake,
        )
    )
    assert out == ["a", "b"]
    assert format_context([hit]) in fake.calls[0]["system"][1]["text"]


def test_model_sonnet_constant() -> None:
    # Verified against platform.claude.com models overview (2026-06-10):
    # dateless pinned snapshot, no date suffix.
    assert MODEL_SONNET == "claude-sonnet-4-6"
