"""Unit tests for legger.chat (prompts + generate) — mocked Anthropic client,
no network.

Pins down: context formatting (header dedup, one block per hit), the marker
instruction in the system prompt, and the message/system assembly sent to the
Anthropic streaming API.
"""

from contextlib import contextmanager
from typing import Any

import pytest

from legger.chat import generate as generate_mod
from legger.chat.generate import MODEL_SONNET, last_user_message, stream_answer
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


class FakeFinalMessage:
    def __init__(self, stop_reason: str) -> None:
        self.stop_reason = stop_reason


class FakeStream:
    """Stands in for the context manager returned by messages.stream()."""

    def __init__(self, deltas: list[str], stop_reason: str = "end_turn") -> None:
        self.text_stream = iter(deltas)
        self._stop_reason = stop_reason

    def get_final_message(self) -> FakeFinalMessage:
        return FakeFinalMessage(self._stop_reason)

    def __enter__(self) -> "FakeStream":
        return self

    def __exit__(self, *exc_info: object) -> None:
        pass


class FakeAnthropic:
    """Records the kwargs of every messages.stream() call."""

    def __init__(self, deltas: list[str] | None = None, stop_reason: str = "end_turn") -> None:
        self.calls: list[dict[str, Any]] = []
        self._deltas = deltas if deltas is not None else ["Risposta ", "grounded."]
        self._stop_reason = stop_reason
        outer = self

        class _Messages:
            @contextmanager
            def stream(self, **kwargs: Any):
                outer.calls.append(kwargs)
                yield FakeStream(outer._deltas, outer._stop_reason)

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


def _drive(gen) -> tuple[list[str], str | None]:
    """Exhaust a stream_answer generator, capturing deltas + return value."""
    deltas: list[str] = []
    while True:
        try:
            deltas.append(next(gen))
        except StopIteration as stop:
            return deltas, stop.value


def test_stream_answer_returns_stop_reason() -> None:
    fake = FakeAnthropic(stop_reason="end_turn")
    gen = stream_answer([{"role": "user", "content": "q"}], [make_hit()], anthropic_client=fake)
    deltas, stop_reason = _drive(gen)
    assert deltas == ["Risposta ", "grounded."]
    assert stop_reason == "end_turn"


def test_stream_answer_signals_truncation_via_stop_reason() -> None:
    fake = FakeAnthropic(deltas=["Risposta tron"], stop_reason="max_tokens")
    gen = stream_answer([{"role": "user", "content": "q"}], [make_hit()], anthropic_client=fake)
    deltas, stop_reason = _drive(gen)
    assert deltas == ["Risposta tron"]
    assert stop_reason == "max_tokens"


def test_model_sonnet_constant() -> None:
    # Verified against platform.claude.com models overview (2026-06-10):
    # dateless pinned snapshot, no date suffix.
    assert MODEL_SONNET == "claude-sonnet-4-6"


# --- beta-testing model/effort overrides ----------------------------------------


def _call_kwargs(fake: FakeAnthropic, **overrides: Any) -> dict[str, Any]:
    messages = [{"role": "user", "content": "q"}]
    list(stream_answer(messages, [make_hit()], anthropic_client=fake, **overrides))
    assert len(fake.calls) == 1
    return fake.calls[0]


def test_default_call_has_no_output_config() -> None:
    # No config sent: today's behavior byte-for-byte (sonnet, temp 0.2,
    # no explicit effort field).
    call = _call_kwargs(FakeAnthropic())
    assert call["model"] == MODEL_SONNET
    assert call["temperature"] == generate_mod.TEMPERATURE
    assert "output_config" not in call


def test_model_override_with_effort() -> None:
    call = _call_kwargs(FakeAnthropic(), model="claude-sonnet-4-6", effort="low")
    assert call["model"] == "claude-sonnet-4-6"
    assert call["temperature"] == generate_mod.TEMPERATURE
    assert call["output_config"] == {"effort": "low"}


def test_opus_48_call_omits_temperature() -> None:
    # temperature is REMOVED on opus-4-8 (400 if sent): the call must not
    # carry it, with or without effort.
    call = _call_kwargs(FakeAnthropic(), model="claude-opus-4-8", effort="max")
    assert call["model"] == "claude-opus-4-8"
    assert "temperature" not in call
    assert call["output_config"] == {"effort": "max"}


def test_haiku_call_omits_output_config() -> None:
    # output_config.effort on haiku-4-5 is a 400: a requested effort is
    # dropped, temperature stays.
    call = _call_kwargs(FakeAnthropic(), model="claude-haiku-4-5", effort="high")
    assert call["model"] == "claude-haiku-4-5"
    assert call["temperature"] == generate_mod.TEMPERATURE
    assert "output_config" not in call
