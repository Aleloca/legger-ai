"""Grounded answer generation over hybrid retrieval (Task C5).

Flow per turn: retrieve on the LAST user message (no query understanding yet
— that is Fase E), format the hits as a context block, then stream a Claude
Sonnet answer constrained by :data:`~legger.chat.prompts.SYSTEM_PROMPT`.

The retrieval and generation halves are exposed separately
(:func:`retrieve_for_messages` / :func:`stream_answer`) because every caller
that streams the answer also needs the hits afterwards — the CLI prints the
"fonti consultate" list, F2 emits them as the SSE ``sources`` event, F3
validates markers against them. :func:`chat_once` composes the two for
callers that only want the text stream.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from legger.chat.prompts import SYSTEM_PROMPT, format_context
from legger.retrieval.search import hybrid_search

if TYPE_CHECKING:
    from collections.abc import Iterator

    from anthropic import Anthropic
    from qdrant_client import QdrantClient

    from legger.retrieval.embedders import Embedder
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


def retrieve_for_messages(
    messages: list[dict],
    *,
    collection: str,
    embedder: Embedder,
    client: QdrantClient,
    k: int = 10,
) -> list[SearchHit]:
    """Hybrid search using the last user message verbatim as the query.

    Follow-ups that only make sense given the previous turns ("e il comma
    successivo?") retrieve poorly by design at this stage: query
    understanding/rewriting arrives in Fase E (E2).
    """
    return hybrid_search(
        last_user_message(messages),
        collection=collection,
        embedder=embedder,
        client=client,
        k=k,
    )


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


def chat_once(
    messages: list[dict],
    *,
    collection: str,
    embedder: Embedder,
    client: QdrantClient,
    anthropic_client: Anthropic,
    k: int = 10,
) -> Iterator[str]:
    """One retrieval-grounded turn: retrieve on the last user message, then
    stream the answer's text deltas."""
    hits = retrieve_for_messages(
        messages, collection=collection, embedder=embedder, client=client, k=k
    )
    yield from stream_answer(messages, hits, anthropic_client=anthropic_client)
