"""Parser for the Normattiva markdown acts of italia-corpus (Task B3).

Entry points:

- ``parse_act(path)``: reads the file, truncates at the first NUL byte (A9),
  detects the base64 Akoma Ntoso HTML format (A1, delegated to ``akn.py``)
  and otherwise parses the pandoc-markdown structure.
- ``parse_act_text(text)``: same, for already-loaded text.

Structural rules implemented (see docs/corpus-analysis.md, A1-A10):

- Setext headings (A2): a line of ``=`` (h1) / ``-`` (h2) underlines the whole
  contiguous non-blank block above it. An underline preceded by a blank line
  or by an ATX heading is a horizontal separator, not a heading.
- Article markers (A4): ATX ``### Art. N``, setext-h2 ``Art. N``, and plain
  ``<Titolo>-art. N [bis|ter|...]`` lines. Suffixes are normalized to the
  hyphen form (``3 bis`` -> ``3-bis``).
- Commi (A5): line-start ``N.`` / ``N-bis.`` with a monotonicity check --
  a numbered line is a new comma only if it sorts strictly after the previous
  accepted comma; this rejects the numbered sub-lists of quoted amendment
  text (legge di bilancio). Articles without numbered commi become a single
  ``Comma(number=None)``.
- Partitions (A6): non-article setext h2 starting with LIBRO/PARTE/TITOLO/
  CAPO/SEZIONE set the current ``Article.path``; any other non-article h2
  (preamble or closing formula) terminates the current article. Non-article
  ATX headings (signature lines such as ``### Dato a Roma, addi' ...``) also
  terminate the current article: what follows is dropped.
- Normattiva conventions (A8): ``((...))`` markers and ``AGGIORNAMENTO (n)``
  blocks are CONTENT and stay inside the article body (the chunker separates
  the aggiornamento notes later). The redactional ``N O T E`` sections are
  cleanly detectable (a line that is exactly ``N O T E``) and are cut out of
  the article body: they reproduce text of *other* acts and would pollute
  retrieval.
"""

import re
from pathlib import Path

from legger.corpus.models import Act, Article, Comma

# Latin ordinal suffixes in their legal order; index = sort rank. Longest-first
# in the alternation so e.g. "quaterdecies" is not half-matched as "quater".
_SUFFIXES = (
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
_SUFFIX_RANK = {suffix: rank for rank, suffix in enumerate(_SUFFIXES, start=1)}
_SUFFIX_ALT = "|".join(sorted(_SUFFIXES, key=len, reverse=True))

_H1_UNDERLINE = re.compile(r"^=+\s*$")
_H2_UNDERLINE = re.compile(r"^-{2,}\s*$")
_ATX = re.compile(r"^#{1,6}\s")
_ATX_ART = re.compile(rf"^###\s+Art\.?\s*(\d+)(?:[ .-]+({_SUFFIX_ALT}))?\.?\s*$")
_SETEXT_ART = re.compile(rf"^Art\.?\s+(\d+)(?:[ .-]+({_SUFFIX_ALT}))?\.?\s*$")
# Plain markers also carry slash numbers ("CODICE CIVILE-art. 314/2": the
# historical CC adoption articles 314/2..314/28), found in the AKN fixture.
_PLAIN_ART = re.compile(rf"^(\S.*?)-art\.\s+(\d+(?:/\d+)?)(?:\s+({_SUFFIX_ALT}))?\s*$")
_COMMA = re.compile(rf"^(\d+)(?:-({_SUFFIX_ALT}))?\.\s")
# "Articolo unico" heading (conversion/ratification laws) -- found in the real
# corpus during B3 validation, not covered by A4: treated as article "unico".
_ART_UNICO = re.compile(r"^Articolo\s+unico\.?\s*$", re.IGNORECASE)
_PARTITION = re.compile(r"^(LIBRO|PARTE|TITOLO|CAPO|SEZIONE)\b", re.IGNORECASE)
_NOTE_LINE = re.compile(r"^N O T E$")
# Rubrica between parentheses (plain/AKN styles); "((" is a Normattiva
# consolidation marker, never a rubrica.
_RUBRIC = re.compile(r"^\((?!\()(.+?)\)\.?$")

_BASE64_HTML_PREFIX = "PGh0bWw"  # base64 of "<html" (A1)


def parse_act(path: Path) -> Act:
    """Parse one corpus file into an :class:`Act`."""
    raw = path.read_bytes()
    # A9: NUL padding -- the useful content is the prefix up to the first NUL.
    raw = raw.split(b"\x00", 1)[0]
    return parse_act_text(raw.decode("utf-8", errors="replace"))


def parse_act_text(text: str) -> Act:
    """Parse act text (markdown or base64-encoded Akoma Ntoso HTML)."""
    text = text.split("\x00", 1)[0]  # A9, for callers that pass raw text
    if text.lstrip().startswith(_BASE64_HTML_PREFIX):
        # Local import: akn.py reuses this module's helpers (commi scanner,
        # marker regexes), so a top-level import would be circular.
        from legger.corpus.akn import parse_akn_text

        return parse_akn_text(text)
    return _parse_markdown(text)


def _normalize_number(base: str, suffix: str | None) -> str:
    return f"{base}-{suffix}" if suffix else base


def _comma_sort_key(base: str, suffix: str | None) -> tuple[int, int]:
    rank = _SUFFIX_RANK.get(suffix, len(_SUFFIXES) + 1) if suffix else 0
    return (int(base), rank)


# ---------------------------------------------------------------------------
# Markdown tokenizer: lines -> (kind, text) events
# ---------------------------------------------------------------------------


def _tokenize(lines: list[str]) -> list[tuple[str, str]]:
    """Classify lines into ``h1``/``h2``/``atx``/``line``/``blank`` events.

    Setext underlines consume the contiguous non-blank block above them (the
    heading text can span several lines, e.g. long act titles). Underlines
    preceded by a blank line or an ATX heading are separators and are dropped.
    """
    events: list[tuple[str, str]] = []
    block: list[str] = []  # pending paragraph lines

    def flush() -> None:
        for pending in block:
            events.append(("line", pending))
        block.clear()

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if not line.strip():
            flush()
            events.append(("blank", ""))
            i += 1
            continue
        if _ATX.match(line):
            flush()
            events.append(("atx", line.strip()))
            # An underline right below an ATX heading (e.g. "### (012U0343)"
            # followed by "----") is a separator, not a setext heading.
            if i + 1 < n and (
                _H1_UNDERLINE.match(lines[i + 1]) or _H2_UNDERLINE.match(lines[i + 1])
            ):
                i += 1
            i += 1
            continue
        if _H1_UNDERLINE.match(line) or _H2_UNDERLINE.match(line):
            if block:
                kind = "h1" if line.lstrip().startswith("=") else "h2"
                events.append((kind, " ".join(part.strip() for part in block)))
                block.clear()
            # else: separator after a blank line -> dropped
            i += 1
            continue
        block.append(line)
        i += 1
    flush()
    return events


# ---------------------------------------------------------------------------
# Markdown state machine: events -> Act
# ---------------------------------------------------------------------------


def _parse_markdown(text: str) -> Act:
    events = _tokenize(text.splitlines())

    title: str | None = None
    subtitle: str | None = None
    path: list[str] = []
    articles: list[Article] = []
    orphan_lines: list[str] = []  # text outside any article (pseudo-article fallback)
    current: dict | None = None

    def close_current() -> None:
        nonlocal current
        if current is not None:
            articles.append(_finish_article(current))
            current = None

    def open_article(number: str, style: str, article_path: list[str]) -> None:
        nonlocal current
        close_current()
        current = {"number": number, "style": style, "path": article_path, "lines": []}

    for kind, payload in events:
        if kind == "h1":
            close_current()
            if title is None:
                title = payload
            continue
        if kind == "atx":
            match = _ATX_ART.match(payload)
            if match:
                open_article(_normalize_number(match.group(1), match.group(2)), "atx", list(path))
            elif _ART_UNICO.match(payload.lstrip("# ")):
                open_article("unico", "atx", list(path))
            else:
                # Signature lines ("### Dato a Roma, addi' ..."): boundary.
                close_current()
            continue
        if kind == "h2":
            match = _SETEXT_ART.match(payload)
            if match:
                open_article(
                    _normalize_number(match.group(1), match.group(2)), "setext", list(path)
                )
                continue
            if _ART_UNICO.match(payload):
                open_article("unico", "setext", list(path))
                continue
            close_current()
            if _PARTITION.match(payload):
                # A6: the corpus exposes a single flat partition level
                # ("CAPO N - <partizione reale>"); keep only the latest.
                path = [payload]
            elif subtitle is None and title is not None:
                subtitle = payload
            continue
        # kind in ("line", "blank")
        if kind == "line":
            match = _PLAIN_ART.match(payload)
            if match:
                open_article(
                    _normalize_number(match.group(2), match.group(3)),
                    "plain",
                    list(path) + [match.group(1)],
                )
                continue
        if current is not None:
            current["lines"].append(payload if kind == "line" else "")
        elif kind == "line":
            orphan_lines.append(payload)

    close_current()

    if not articles:
        # A10: no detectable articles -> single pseudo-article so that
        # downstream consumers always see >= 1 article.
        body = "\n".join(orphan_lines).strip("\n")
        articles = [Article(number="unico", commi=[Comma(number=None, text=body)])]

    return Act(title=title, subtitle=subtitle, source_format="markdown", articles=articles)


# ---------------------------------------------------------------------------
# Article assembly (shared with the AKN path)
# ---------------------------------------------------------------------------


def _finish_article(current: dict) -> Article:
    lines: list[str] = current["lines"]
    lines = _cut_note_section(lines)
    heading: str | None = None
    if current["style"] == "atx":
        heading, lines = _extract_atx_rubric(lines)
    elif current["style"] in ("plain", "akn"):
        heading, lines = _extract_paren_rubric(lines)
    commi = _scan_commi(lines)
    return Article(number=current["number"], heading=heading, path=current["path"], commi=commi)


def _cut_note_section(lines: list[str]) -> list[str]:
    """Drop the trailing GU redactional notes (a line that is exactly ``N O T E``)."""
    for i, line in enumerate(lines):
        if _NOTE_LINE.match(line.strip()):
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
    while j < len(lines) and lines[j].strip() and not _COMMA.match(lines[j]):
        captured.append(lines[j].strip())
        j += 1
    if not captured:
        return None, lines
    return " ".join(captured), lines[:i] + lines[j:]


def _extract_paren_rubric(lines: list[str]) -> tuple[str | None, list[str]]:
    """Rubrica of plain/AKN articles: a ``(...)`` line near the body start."""
    seen_nonempty = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        match = _RUBRIC.match(stripped)
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
        match = _COMMA.match(line)
        if match:
            key = _comma_sort_key(match.group(1), match.group(2))
            if key > prev_key:
                flush()
                bucket_number = _normalize_number(match.group(1), match.group(2))
                prev_key = key
                found_any = True
        bucket.append(line)
    flush()

    if not found_any:
        text = "\n".join(lines).strip("\n").strip()
        return [Comma(number=None, text=text)]
    return commi
