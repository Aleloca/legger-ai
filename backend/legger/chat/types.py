"""Shared chat types.

:class:`Message` is THE conversation-turn shape that flows through the whole
chat stack — CLI REPL, F2 ``POST /chat``, query understanding, the retrieval
pipeline, generation. It is a :class:`~typing.TypedDict` (not a pydantic
model) on purpose: the dicts are also sent verbatim as the ``messages``
parameter of the Anthropic API, so they must stay plain dicts.
"""

from __future__ import annotations

from typing import Literal, TypedDict


class Message(TypedDict):
    """One conversation turn, in Anthropic ``messages`` format."""

    role: Literal["user", "assistant"]
    content: str
