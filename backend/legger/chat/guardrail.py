"""Citation guardrail: verify a marker against the retrieval hits (Task F3).

:func:`check_citation` answers "did the model cite something it was actually
shown?" by matching a :class:`~legger.chat.stream.ParsedMarker` against the
hits that built the generation context. Three granularities, two severities:

- **act** and **article** are hard checks: a miss means the model cited a
  provision that was not in its context, so ``verified`` is False and the
  UI should flag the citation (reasons ``act_not_in_context`` /
  ``article_not_in_context``).
- **comma** is advisory: when the act+article match but the marker's comma
  is not in any matching hit's ``commi`` payload list, the reason is
  ``comma_not_in_context`` while ``verified`` stays True. The commi lists
  are structurally incomplete — they are empty for unnumbered text
  (historical acts index with ``commi == []``, see
  :class:`legger.corpus.chunker.Chunk`) and a split article's hits each
  carry only their own slice of commi — so a hard fail here would raise
  false alarms on correct citations. The UI may render a softer hint.

Matching is string equality after lowercase normalization, so suffixed
numbers ("62-bis", comma "2-sexies") compare exactly and never match their
base number.

PRECONDITION: ``marker`` comes from :func:`legger.chat.stream.parse_marker`,
which only accepts the strict contract format — ``act_ref`` and ``article``
are non-empty and well-formed by construction. Malformed bracket pairs are
rejected upstream (no citation event is emitted at all) and never reach this
function.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

from legger.retrieval.search import SearchHit

if TYPE_CHECKING:
    from legger.chat.stream import ParsedMarker

#: Why a citation did (or did not) verify — see the module docstring.
Reason = Literal["ok", "act_not_in_context", "article_not_in_context", "comma_not_in_context"]


class CitationCheck(BaseModel):
    """The guardrail verdict for one citation marker.

    ``hit`` is the matching hit for title/vigenza enrichment: the hit whose
    commi contain the marker's comma when there is one, else the first
    act+article match; ``None`` when act/article verification fails.
    """

    verified: bool
    reason: Reason
    hit: SearchHit | None


def _norm(value: str) -> str:
    return value.strip().lower()


def _commi(hit: SearchHit) -> list[str]:
    """The hit's commi payload list (empty for unnumbered text)."""
    return [_norm(str(comma)) for comma in hit.payload.get("commi") or []]


def check_citation(marker: ParsedMarker, hits: list[SearchHit]) -> CitationCheck:
    """Verify one parsed marker against the retrieval hits.

    See the module docstring for the matching rules, the advisory comma
    semantics, and the precondition (``marker`` is parser output).
    """
    act_matches = [hit for hit in hits if _norm(hit.act_ref) == _norm(marker.act_ref)]
    if not act_matches:
        return CitationCheck(verified=False, reason="act_not_in_context", hit=None)

    article_matches = [hit for hit in act_matches if _norm(hit.article) == _norm(marker.article)]
    if not article_matches:
        return CitationCheck(verified=False, reason="article_not_in_context", hit=None)

    if marker.comma is None:
        return CitationCheck(verified=True, reason="ok", hit=article_matches[0])

    comma = _norm(marker.comma)
    for hit in article_matches:
        if comma in _commi(hit):
            return CitationCheck(verified=True, reason="ok", hit=hit)

    # Advisory: the act+article were in context, only the comma granularity
    # could not be confirmed (verified stays True — see module docstring).
    return CitationCheck(verified=True, reason="comma_not_in_context", hit=article_matches[0])
