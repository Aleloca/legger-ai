"""Grounded answer generation (Task C5, retrieval side superseded by E5).

Retrieval lives in :func:`legger.retrieval.pipeline.retrieve` (the unified E5
pipeline); this module keeps only the generation half: :func:`stream_answer`
turns retrieved hits into a streamed Claude Sonnet answer constrained by
:data:`~legger.chat.prompts.SYSTEM_PROMPT`. Callers that stream the answer
also need the hits afterwards — the CLI prints the "fonti consultate" list,
F2 emits them as the SSE ``sources`` event, F3 validates markers against
them — which is why generation takes ``hits`` instead of retrieving itself.

:func:`last_user_message` stays here too: it predates the pipeline and the
pipeline imports it (the retrieval query selector).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from legger.chat.prompts import SYSTEM_PROMPT, format_context

if TYPE_CHECKING:
    from collections.abc import Iterator

    from anthropic import Anthropic

    from legger.retrieval.search import SearchHit

#: Current Claude Sonnet id, verified against the models overview on
#: platform.claude.com (2026-06-10): "claude-sonnet-4-6" is both the Claude
#: API ID and the alias — dateless pinned snapshot, do NOT append a date.
MODEL_SONNET = "claude-sonnet-4-6"

MAX_TOKENS = 1500
TEMPERATURE = 0.2


def last_user_message(messages: list[dict]) -> str:
    """The content of the most recent ``user`` turn (the retrieval query)."""
    for message in reversed(messages):
        if message["role"] == "user":
            return message["content"]
    raise ValueError("chat: no user message in the conversation")


def stream_answer(
    messages: list[dict],
    hits: list[SearchHit],
    *,
    anthropic_client: Anthropic,
) -> Iterator[str]:
    """Stream the grounded answer for ``messages`` given retrieved ``hits``.

    The system prompt and the normative context go as two system blocks: the
    prompt is stable (cache breakpoint on it — free now, pays off once F2
    serves concurrent conversations), the context block changes every turn
    and therefore sits after the breakpoint. Yields text deltas.
    """
    context_block = (
        "Passaggi normativi recuperati per la domanda corrente "
        "(la tua UNICA fonte ammessa):\n\n"
        f"<contesto>\n{format_context(hits)}\n</contesto>"
    )
    with anthropic_client.messages.stream(
        model=MODEL_SONNET,
        max_tokens=MAX_TOKENS,
        temperature=TEMPERATURE,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            },
            {"type": "text", "text": context_block},
        ],
        messages=messages,
    ) as stream:
        yield from stream.text_stream
