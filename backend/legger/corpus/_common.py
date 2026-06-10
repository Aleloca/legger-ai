"""Internals shared by the markdown (``parser.py``) and AKN (``akn.py``) paths.

Both parsers produce articles through the same pipeline: an
:class:`ArticleDraft` (the article-in-progress mapping) is filled with raw
body lines and then turned into an :class:`Article` by :func:`finish_article`
(N O T E cut, rubrica extraction, commi scan). The marker grammar is likewise
shared: AKN ``article-num-akn`` headings and ``attachment-name`` divs carry
the same text shapes as the markdown setext/plain markers (A4), so the
``SETEXT_ART`` / ``PLAIN_ART`` regexes and the suffix table live here.
"""

import re
from typing import Literal, TypedDict

from legger.corpus.models import Article, Comma

# Latin ordinal suffixes in their legal order; index = sort rank. Longest-first
# in the alternation so e.g. "quaterdecies" is not half-matched as "quater".
SUFFIXES = (
    "bis",
    "ter",
    "quater",
    "quinquies",
    "sexies",
    "septies",
    "octies",
    "novies",
    "decies",
    "undecies",
    "duodecies",
    "terdecies",
    "quaterdecies",
    "quindecies",
    "quinquiesdecies",
    "sexiesdecies",
    "septiesdecies",
    "duodevicies",
    "octiesdecies",
    "noviesdecies",
    "vicies",
)
SUFFIX_RANK = {suffix: rank for rank, suffix in enumerate(SUFFIXES, start=1)}
SUFFIX_ALT = "|".join(sorted(SUFFIXES, key=len, reverse=True))

SETEXT_ART = re.compile(rf"^Art\.?\s+(\d+)(?:[ .-]+({SUFFIX_ALT}))?\.?\s*$")
# Plain markers also carry slash numbers ("CODICE CIVILE-art. 314/2": the
# historical CC adoption articles 314/2..314/28), found in the AKN fixture.
PLAIN_ART = re.compile(rf"^(\S.*?)-art\.\s+(\d+(?:/\d+)?)(?:\s+({SUFFIX_ALT}))?\s*$")
# The \s after the dot is load-bearing: it requires whitespace, so decimals
# and dotted references ("1.500", "2.1") never open a spurious comma.
COMMA = re.compile(rf"^(\d+)(?:-({SUFFIX_ALT}))?\.\s")
NOTE_LINE = re.compile(r"^N O T E$")
# Rubrica between parentheses (plain/AKN styles); "((" is a Normattiva
# consolidation marker, never a rubrica.
RUBRIC = re.compile(r"^\((?!\()(.+?)\)\.?$")


class ArticleDraft(TypedDict):
    """An article in progress: marker info plus the raw body lines.

    ``style`` records which marker grammar opened the article and selects the
    rubrica-extraction strategy in :func:`finish_article`.
    """

    number: str
    style: Literal["atx", "setext", "plain", "akn"]
    path: list[str]
    lines: list[str]


def normalize_number(base: str, suffix: str | None) -> str:
    return f"{base}-{suffix}" if suffix else base


def comma_sort_key(base: str, suffix: str | None) -> tuple[int, int]:
    rank = SUFFIX_RANK.get(suffix, len(SUFFIXES) + 1) if suffix else 0
    return (int(base), rank)


# ---------------------------------------------------------------------------
# Article assembly: draft -> Article
# ---------------------------------------------------------------------------


def finish_article(draft: ArticleDraft) -> Article:
    lines = _cut_note_section(draft["lines"])
    heading: str | None = None
    if draft["style"] == "atx":
        heading, lines = _extract_atx_rubric(lines)
    elif draft["style"] in ("plain", "akn"):
        heading, lines = _extract_paren_rubric(lines)
    commi = _scan_commi(lines)
    return Article(number=draft["number"], heading=heading, path=draft["path"], commi=commi)


def _cut_note_section(lines: list[str]) -> list[str]:
    """Drop the trailing GU redactional notes (a line that is exactly ``N O T E``)."""
    for i, line in enumerate(lines):
        if NOTE_LINE.match(line.strip()):
            return lines[:i]
    return lines


def _extract_atx_rubric(lines: list[str]) -> tuple[str | None, list[str]]:
    """Rubrica of ATX articles: the first non-blank block after the marker.

    Stops at a blank line or at the first comma line (articles whose body
    starts directly with ``1.`` have no rubrica).
    """
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    captured: list[str] = []
    j = i
    while j < len(lines) and lines[j].strip() and not COMMA.match(lines[j]):
        captured.append(lines[j].strip())
        j += 1
    if not captured:
        return None, lines
    return " ".join(captured), lines[:i] + lines[j:]


def _extract_paren_rubric(lines: list[str]) -> tuple[str | None, list[str]]:
    """Rubrica of plain/AKN articles: a ``(...)`` line near the body start.

    Stops at the first comma line (like the ATX variant): once the numbered
    body has started, a parenthesized line is content, not a rubrica.
    """
    seen_nonempty = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if COMMA.match(stripped):
            break
        match = RUBRIC.match(stripped)
        if match:
            return match.group(1), lines[:i] + lines[i + 1 :]
        seen_nonempty += 1
        if seen_nonempty >= 4:
            break
    return None, lines


def _scan_commi(lines: list[str]) -> list[Comma]:
    """Split an article body into commi (A5, best-effort).

    A line-start ``N.`` / ``N-bis.`` opens a new comma only if (base, suffix)
    sorts strictly after the previous accepted comma; rejected candidates
    (numbered sub-lists in quoted amendment text) stay inside the current
    comma. Leading un-numbered text becomes a ``Comma(number=None)``;
    trailing material (e.g. AGGIORNAMENTO blocks) stays in the last comma.
    """
    commi: list[Comma] = []
    bucket: list[str] = []
    bucket_number: str | None = None
    prev_key = (0, 0)
    found_any = False

    def flush() -> None:
        nonlocal bucket
        text = "\n".join(bucket).strip("\n").rstrip()
        if bucket_number is not None or text.strip():
            commi.append(Comma(number=bucket_number, text=text))
        bucket = []

    for line in lines:
        match = COMMA.match(line)
        if match:
            key = comma_sort_key(match.group(1), match.group(2))
            if key > prev_key:
                flush()
                bucket_number = normalize_number(match.group(1), match.group(2))
                prev_key = key
                found_any = True
        bucket.append(line)
    flush()

    if not found_any:
        text = "\n".join(lines).strip("\n").strip()
        return [Comma(number=None, text=text)]
    return commi
