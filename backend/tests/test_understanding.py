"""Unit tests for legger.chat.understanding — mocked Anthropic client, no network.

Pins down: the forced-tool request assembly (model, tool_choice, system
prompt, conversation truncation), tool_use parsing into the typed model, and
the never-raise fallback contract (API error / missing tool_use /
schema-invalid input all degrade to the verbatim last user message).
"""

from types import SimpleNamespace
from typing import Any

import pytest

from legger.chat.understanding import (
    ANALYZE_QUERY_TOOL,
    HISTORY_CHAR_LIMIT,
    HISTORY_TURNS,
    MODEL_HAIKU,
    QueryAnalysis,
    RefHint,
    understand_query,
)


def tool_use_block(input_: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", name="analyze_query", input=input_)


def text_block(text: str = "non dovrei essere qui") -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


class FakeAnthropic:
    """Records with_options + messages.create kwargs; returns canned content."""

    def __init__(
        self, content: list[SimpleNamespace] | None = None, exc: Exception | None = None
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self.option_calls: list[dict[str, Any]] = []
        self._content = content if content is not None else []
        self._exc = exc
        outer = self

        class _Messages:
            def create(self, **kwargs: Any) -> SimpleNamespace:
                outer.calls.append(kwargs)
                if outer._exc is not None:
                    raise outer._exc
                return SimpleNamespace(content=outer._content)

        self.messages = _Messages()

    def with_options(self, **kwargs: Any) -> "FakeAnthropic":
        self.option_calls.append(kwargs)
        return self


def messages_one_turn(content: str = "cosa dice l'art. 2051 c.c.?") -> list[dict]:
    return [{"role": "user", "content": content}]


# --- happy path: tool_use -> typed model --------------------------------------


def test_tool_use_parses_into_query_analysis() -> None:
    fake = FakeAnthropic(
        content=[
            tool_use_block(
                {
                    "rewritten_query": "art. 2052 del codice civile responsabilità animali",
                    "explicit_refs": [{"act": "codice civile", "article": "2052"}],
                    "temporal_reference": None,
                    "wants_historical": False,
                }
            )
        ]
    )
    analysis = understand_query(
        [
            {"role": "user", "content": "cosa dice l'art. 2051 c.c.?"},
            {"role": "assistant", "content": "L'art. 2051 c.c. disciplina..."},
            {"role": "user", "content": "e l'articolo successivo?"},
        ],
        anthropic_client=fake,
    )
    assert analysis == QueryAnalysis(
        rewritten_query="art. 2052 del codice civile responsabilità animali",
        explicit_refs=[RefHint(act="codice civile", article="2052")],
        temporal_reference=None,
        wants_historical=False,
    )


def test_omitted_optional_fields_get_defaults() -> None:
    # Haiku may emit only the required field; the optional ones default.
    fake = FakeAnthropic(content=[tool_use_block({"rewritten_query": "legittima difesa"})])
    analysis = understand_query(messages_one_turn(), anthropic_client=fake)
    assert analysis.rewritten_query == "legittima difesa"
    assert analysis.explicit_refs == []
    assert analysis.temporal_reference is None
    assert analysis.wants_historical is False


def test_tool_use_found_after_other_blocks() -> None:
    fake = FakeAnthropic(content=[text_block(), tool_use_block({"rewritten_query": "q"})])
    analysis = understand_query(messages_one_turn(), anthropic_client=fake)
    assert analysis.rewritten_query == "q"


# --- fallback contract: the chat never breaks for QU --------------------------


def test_api_exception_falls_back_to_verbatim_message() -> None:
    fake = FakeAnthropic(exc=RuntimeError("API down"))
    analysis = understand_query(messages_one_turn("e il comma successivo?"), anthropic_client=fake)
    assert analysis == QueryAnalysis(rewritten_query="e il comma successivo?")


def test_missing_tool_use_block_falls_back() -> None:
    fake = FakeAnthropic(content=[text_block()])
    analysis = understand_query(messages_one_turn("query verbatim"), anthropic_client=fake)
    assert analysis == QueryAnalysis(rewritten_query="query verbatim")


def test_schema_invalid_tool_input_falls_back() -> None:
    # rewritten_query missing entirely: pydantic validation must fail closed.
    fake = FakeAnthropic(content=[tool_use_block({"explicit_refs": []})])
    analysis = understand_query(messages_one_turn("query verbatim"), anthropic_client=fake)
    assert analysis == QueryAnalysis(rewritten_query="query verbatim")


def test_wrong_type_tool_input_falls_back() -> None:
    fake = FakeAnthropic(
        content=[tool_use_block({"rewritten_query": "q", "explicit_refs": "not-a-list"})]
    )
    analysis = understand_query(messages_one_turn("query verbatim"), anthropic_client=fake)
    assert analysis == QueryAnalysis(rewritten_query="query verbatim")


def test_fallback_uses_last_user_turn_not_last_message() -> None:
    fake = FakeAnthropic(exc=RuntimeError("boom"))
    analysis = understand_query(
        [
            {"role": "user", "content": "la vera domanda"},
            {"role": "assistant", "content": "risposta parziale"},
        ],
        anthropic_client=fake,
    )
    assert analysis.rewritten_query == "la vera domanda"


# --- request assembly ----------------------------------------------------------


def test_request_forces_the_analyze_query_tool() -> None:
    fake = FakeAnthropic(content=[tool_use_block({"rewritten_query": "q"})])
    understand_query(messages_one_turn(), anthropic_client=fake)
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["model"] == MODEL_HAIKU
    assert call["tool_choice"] == {"type": "tool", "name": "analyze_query"}
    assert call["tools"] == [ANALYZE_QUERY_TOOL]
    assert call["max_tokens"] == 500
    assert call["temperature"] == 0.0


def test_short_timeout_and_zero_retries() -> None:
    # max_retries=0: worst case is one attempt (~10s); the fallback is cheap,
    # so a retry would only double the latency budget on the chat hot path.
    fake = FakeAnthropic(content=[tool_use_block({"rewritten_query": "q"})])
    understand_query(messages_one_turn(), anthropic_client=fake)
    assert fake.option_calls == [{"timeout": 10.0, "max_retries": 0}]


def test_system_prompt_is_analysis_only() -> None:
    fake = FakeAnthropic(content=[tool_use_block({"rewritten_query": "q"})])
    understand_query(messages_one_turn(), anthropic_client=fake)
    system = fake.calls[0]["system"]
    # The task is the rewrite/extraction, with an explicit do-not-answer rule:
    # the QU model must never leak answer-the-question behavior.
    assert "NON rispondere" in system
    assert "query understanding" in system
    assert "anaforici" in system


def test_conversation_truncated_to_last_six_turns() -> None:
    fake = FakeAnthropic(content=[tool_use_block({"rewritten_query": "q"})])
    history = []
    for i in range(10):
        role = "user" if i % 2 == 0 else "assistant"
        history.append({"role": role, "content": f"turno {i}"})
    messages = [*history, {"role": "user", "content": "domanda corrente"}]

    understand_query(messages, anthropic_client=fake)

    prompt = fake.calls[0]["messages"][0]["content"]
    assert "domanda corrente" in prompt
    # Exactly the HISTORY_TURNS most recent prior turns survive.
    for i in range(10 - HISTORY_TURNS, 10):
        assert f"turno {i}" in prompt
    for i in range(10 - HISTORY_TURNS):
        assert f"turno {i}" not in prompt
    # Compact role labels in Italian.
    assert "utente: turno 8" in prompt
    assert "assistente: turno 9" in prompt


def test_single_turn_conversation_has_empty_history() -> None:
    fake = FakeAnthropic(content=[tool_use_block({"rewritten_query": "q"})])
    understand_query(messages_one_turn("solo questa"), anthropic_client=fake)
    prompt = fake.calls[0]["messages"][0]["content"]
    assert "<storico>\n(nessuno)\n</storico>" in prompt
    assert "<messaggio_corrente>\nsolo questa\n</messaggio_corrente>" in prompt


def test_history_and_current_message_are_tagged() -> None:
    # Distinct XML-ish delimiters keep message text from spoofing the framing.
    fake = FakeAnthropic(content=[tool_use_block({"rewritten_query": "q"})])
    understand_query(
        [
            {"role": "user", "content": "prima domanda"},
            {"role": "assistant", "content": "prima risposta"},
            {"role": "user", "content": "domanda corrente"},
        ],
        anthropic_client=fake,
    )
    prompt = fake.calls[0]["messages"][0]["content"]
    storico = prompt.split("<storico>\n")[1].split("\n</storico>")[0]
    corrente = prompt.split("<messaggio_corrente>\n")[1].split("\n</messaggio_corrente>")[0]
    assert storico == "utente: prima domanda\nassistente: prima risposta"
    assert corrente == "domanda corrente"


def test_history_messages_clamped_to_char_limit() -> None:
    # Each history message is cut at HISTORY_CHAR_LIMIT chars: a deterministic
    # bound on prompt size (cost/latency). The current message is not clamped.
    fake = FakeAnthropic(content=[tool_use_block({"rewritten_query": "q"})])
    long_history = "h" * (HISTORY_CHAR_LIMIT + 500)
    current = "c" * (HISTORY_CHAR_LIMIT + 500)
    understand_query(
        [
            {"role": "user", "content": long_history},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": current},
        ],
        anthropic_client=fake,
    )
    prompt = fake.calls[0]["messages"][0]["content"]
    assert "h" * HISTORY_CHAR_LIMIT in prompt
    assert "h" * (HISTORY_CHAR_LIMIT + 1) not in prompt
    assert current in prompt


def test_non_string_content_does_not_break_the_fallback() -> None:
    # Issue: content may be a list of blocks (Anthropic-style); the fallback
    # (and the prompt assembly) must coerce via str() instead of raising.
    fake = FakeAnthropic(exc=RuntimeError("API down"))
    content = [{"type": "text", "text": "domanda"}]
    analysis = understand_query([{"role": "user", "content": content}], anthropic_client=fake)
    assert analysis == QueryAnalysis(rewritten_query=str(content))


def test_tool_schema_mirrors_query_analysis_fields() -> None:
    # Guard against schema drift: every QueryAnalysis field must exist in the
    # forced-tool schema and vice versa.
    assert set(ANALYZE_QUERY_TOOL["input_schema"]["properties"]) == set(QueryAnalysis.model_fields)


def test_default_call_has_no_output_config() -> None:
    # No config sent: today's behavior (haiku, temp 0.0, no effort field).
    fake = FakeAnthropic(content=[tool_use_block({"rewritten_query": "q"})])
    understand_query(messages_one_turn(), anthropic_client=fake)
    call = fake.calls[0]
    assert call["model"] == MODEL_HAIKU
    assert call["temperature"] == 0.0
    assert "output_config" not in call


def test_model_override_sonnet_with_effort() -> None:
    fake = FakeAnthropic(content=[tool_use_block({"rewritten_query": "q"})])
    understand_query(
        messages_one_turn(), anthropic_client=fake, model="claude-sonnet-4-6", effort="low"
    )
    call = fake.calls[0]
    assert call["model"] == "claude-sonnet-4-6"
    assert call["temperature"] == 0.0  # QU stays deterministic on any model
    assert call["output_config"] == {"effort": "low"}


def test_haiku_with_effort_drops_output_config() -> None:
    # output_config.effort on haiku-4-5 is a 400 at the API: dropped here.
    fake = FakeAnthropic(content=[tool_use_block({"rewritten_query": "q"})])
    understand_query(
        messages_one_turn(), anthropic_client=fake, model="claude-haiku-4-5", effort="max"
    )
    call = fake.calls[0]
    assert call["model"] == "claude-haiku-4-5"
    assert "output_config" not in call


def test_model_haiku_constant() -> None:
    # Verified against platform.claude.com models overview (2026-06-11):
    # alias claude-haiku-4-5 (full id claude-haiku-4-5-20251001).
    assert MODEL_HAIKU == "claude-haiku-4-5"


def test_no_user_message_raises_caller_bug() -> None:
    # Not a QU failure: a conversation without any user turn is a caller bug
    # (generate.last_user_message has the same contract).
    with pytest.raises(ValueError):
        understand_query([{"role": "assistant", "content": "x"}], anthropic_client=FakeAnthropic())
