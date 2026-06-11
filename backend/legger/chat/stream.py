"""Incremental citation-marker parser for the SSE chat stream (Task F2).

The model emits citation markers in the ``[[act_ref|art.N|c.M]]`` contract
format (see :data:`legger.chat.prompts.SYSTEM_PROMPT`), but the Anthropic
stream slices text at arbitrary points — a marker routinely arrives split
across two or three deltas. :class:`MarkerParser` re-assembles the stream
into a sequence of :class:`TextPiece` / :class:`MarkerPiece` so the API
layer can emit each complete marker both as token text and as a structured
``citation`` event.

Buffering policy: text flows through immediately — the parser only holds
back (a) a single trailing ``[`` (it may become ``[[`` with the next delta)
and (b) everything between an opening ``[[`` and its ``]]``, capped at
:data:`MARKER_BUFFER_CAP` characters. A runaway ``[[`` that never closes is
flushed back out as plain text (byte-exact) and scanning resumes, so a
stray bracket pair can never stall the stream or swallow output. The
concatenation of all piece payloads is always byte-identical to the input.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Max characters buffered while waiting for a marker's closing ``]]``.
#: Real markers are <60 chars; past this the ``[[`` was a false alarm.
MARKER_BUFFER_CAP = 200

_OPEN = "[["
_CLOSE = "]]"

#: Strict contract format: ``[[act_ref|art.N]]`` or ``[[act_ref|art.N|c.M]]``,
#: with act_ref a lowercase slug (same alphabet as the pipeline's marker
#: regex). Anything else inside ``[[...]]`` is NOT a citation.
_MARKER_RE = re.compile(r"\A\[\[([a-z0-9-]+)\|art\.([^|\[\]\s]+)(?:\|c\.([^|\[\]\s]+))?\]\]\Z")


@dataclass(frozen=True)
class TextPiece:
    """A run of plain answer text."""

    text: str


@dataclass(frozen=True)
class MarkerPiece:
    """One complete ``[[...]]`` marker, brackets included."""

    raw: str


Piece = TextPiece | MarkerPiece


@dataclass(frozen=True)
class ParsedMarker:
    """The fields of a contract-format marker (see :func:`parse_marker`)."""

    act_ref: str
    article: str
    comma: str | None


def parse_marker(raw: str) -> ParsedMarker | None:
    """Parse a complete ``[[...]]`` marker into its citation fields.

    Returns ``None`` when the bracket pair does not match the contract
    format — the caller then treats it as plain text (no citation event).
    """
    match = _MARKER_RE.match(raw)
    if match is None:
        return None
    act_ref, article, comma = match.groups()
    return ParsedMarker(act_ref=act_ref, article=article, comma=comma)


class MarkerParser:
    """Incremental splitter of a text stream into text and marker pieces.

    Feed deltas with :meth:`feed` (each call returns the pieces completed so
    far); call :meth:`flush` once at end-of-stream to release anything still
    buffered (a trailing ``[`` or an unterminated marker) as plain text.
    """

    def __init__(self) -> None:
        self._buf = ""
        self._in_marker = False

    def feed(self, delta: str) -> list[Piece]:
        """Consume one stream delta, returning the pieces it completed."""
        self._buf += delta
        out: list[Piece] = []
        while True:
            if self._in_marker:
                close = self._buf.find(_CLOSE, len(_OPEN))
                if close != -1 and close + len(_CLOSE) <= MARKER_BUFFER_CAP:
                    out.append(MarkerPiece(self._buf[: close + len(_CLOSE)]))
                    self._buf = self._buf[close + len(_CLOSE) :]
                    self._in_marker = False
                    continue
                if close != -1 or len(self._buf) > MARKER_BUFFER_CAP:
                    # Runaway marker (no "]]" within the cap): release the
                    # opening "[[" as text and rescan the rest in normal
                    # mode — an inner "[[" (a real marker hiding behind the
                    # false alarm) is recovered.
                    out.append(TextPiece(_OPEN))
                    self._buf = self._buf[len(_OPEN) :]
                    self._in_marker = False
                    continue
                break  # wait for more deltas
            opening = self._buf.find(_OPEN)
            if opening != -1:
                if opening:
                    out.append(TextPiece(self._buf[:opening]))
                self._buf = self._buf[opening:]
                self._in_marker = True
                continue
            # No "[[": emit everything except a trailing "[" (which may
            # become "[[" with the next delta).
            if self._buf.endswith("["):
                if len(self._buf) > 1:
                    out.append(TextPiece(self._buf[:-1]))
                    self._buf = "["
            elif self._buf:
                out.append(TextPiece(self._buf))
                self._buf = ""
            break
        return out

    def flush(self) -> list[Piece]:
        """Release whatever is still buffered (end-of-stream) as plain text."""
        out: list[Piece] = [TextPiece(self._buf)] if self._buf else []
        self._buf = ""
        self._in_marker = False
        return out
