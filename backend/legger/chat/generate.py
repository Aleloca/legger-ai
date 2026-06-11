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

from legger.chat.models_catalog import build_model_kwargs
from legger.chat.prompts import SYSTEM_PROMPT, format_context

if TYPE_CHECKING:
    from collections.abc import Generator

    from anthropic import Anthropic

    from legger.chat.types import Message
    from legger.retrieval.search import SearchHit

#: Current Claude Sonnet id, verified against the models overview on
#: platform.claude.com (2026-06-10): "claude-sonnet-4-6" is both the Claude
#: API ID and the alias — dateless pinned snapshot, do NOT append a date.
MODEL_SONNET = "claude-sonnet-4-6"

MAX_TOKENS = 2048
TEMPERATURE = 0.2


def last_user_message(messages: list[Message]) -> str:
    """The content of the most recent ``user`` turn (the retrieval query)."""
    for message in reversed(messages):
        if message["role"] == "user":
            return message["content"]
    raise ValueError("chat: no user message in the conversation")


def stream_answer(
    messages: list[Message],
    hits: list[SearchHit],
    *,
    anthropic_client: Anthropic,
    model: str | None = None,
    effort: str | None = None,
) -> Generator[str, None, str | None]:
    """Stream the grounded answer for ``messages`` given retrieved ``hits``.

    Yields text deltas, then RETURNS the final ``stop_reason`` as the
    generator's return value (``StopIteration.value``). ``"max_tokens"``
    means the answer was truncated mid-sentence — callers must surface that
    (the CLI prints a truncation note, F2 sets ``truncated`` on the ``done``
    SSE event). Capture it either by driving the generator with ``next()``
    and catching ``StopIteration``, or with ``stop = yield from
    stream_answer(...)`` when delegating from another generator. A plain
    ``for`` loop silently discards it — fine only for callers that do not
    care about truncation.

    ``model``/``effort`` are the per-conversation beta-testing overrides
    (F2 ``ChatRequest.config``): ``model`` must come from
    :data:`~legger.chat.models_catalog.ALLOWED_ANSWER_MODELS` (the API layer
    validates; this function trusts its caller), ``None`` keeps today's
    default (:data:`MODEL_SONNET`, no explicit effort). The per-model API
    constraints — no ``temperature`` on opus-4-8, no ``output_config`` on
    haiku-4-5 — are applied by
    :func:`~legger.chat.models_catalog.build_model_kwargs`.

    The system prompt and the normative context go as two system blocks: the
    prompt is stable, the context block changes every turn and therefore
    sits after the cache breakpoint. NOTE the breakpoint is currently a
    no-op: SYSTEM_PROMPT is ~650 tokens, below Sonnet 4.6's 2048-token
    minimum cacheable prefix, so it silently does not cache (no error, no
    cost). Kept for when the prompt grows past the threshold.
    """
    context_block = (
        "Passaggi normativi recuperati per la domanda corrente "
        "(la tua UNICA fonte ammessa):\n\n"
        f"<contesto>\n{format_context(hits)}\n</contesto>"
    )
    with anthropic_client.messages.stream(
        **build_model_kwargs(model or MODEL_SONNET, effort, temperature=TEMPERATURE),
        max_tokens=MAX_TOKENS,
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
        return stream.get_final_message().stop_reason
