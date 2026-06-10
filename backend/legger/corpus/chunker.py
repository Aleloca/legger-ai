"""Article-based chunker with contextual headers (Task B5, design §4.2).

The chunk is the retrieval unit: ``Chunk.text`` is what gets embedded
(dense + sparse) and what the LLM sees as context; the payload fields drive
filters (``vigenza``), citations (``act_ref``/``article``/``commi``) and the
split-view deep-link.

Chunking rules
==============
1. Default: 1 article = 1 chunk.
2. Split threshold: an article with more than :data:`SPLIT_MAX_COMMI` commi
   OR a body longer than :data:`SPLIT_MAX_BODY` chars is split into groups
   of commi with an overlap of exactly 1 comma between consecutive chunks.
   Groups are filled greedily up to :data:`TARGET_BODY` (~4.5k chars, the
   middle of the 3-5k embedding-window target), with one allowance: a group
   may always take a second comma up to :data:`MAX_BODY` so that an
   oversized boundary comma never strands a tiny chunk. The overlap comma is
   dropped at a boundary only when the two adjacent commi together would
   exceed :data:`MAX_BODY` (pathological adjacent giants).
3. Commi longer than :data:`MAX_BODY` (and articles with a single huge
   unnumbered comma) are pre-split at paragraph boundaries (blank lines),
   then at line boundaries, then -- last resort, e.g. a single 30k-char
   line -- at hard character offsets. Every piece keeps the comma label of
   the comma it came from, and units are rejoined with the separator they
   had in the source (line-derived units with a single newline, never an
   inflated blank line).
4. Cap: ``len(text) <= MAX_TEXT`` (8000) always holds: the body is capped at
   :data:`MAX_BODY` (7600) and the header at :data:`MAX_HEADER` (300), so no
   chunk ever breaks the embedding window.

Contextual header (feeds embedding AND citations)
=================================================
Up to three lines, total <= :data:`MAX_HEADER` chars (long pieces are
truncated at a word boundary with an ellipsis)::

    {TIPO ESTESO} {numero}/{anno} — {titolo atto}
    {partition path, when present (e.g. "CAPO V - ..." or "CODICE CIVILE")}
    Art. {number}{ — rubrica}{ (commi N–M) | (parte k/n), when split}

- The tipo esteso is humanized from the ``act_type`` slug
  (``decreto_legislativo`` -> ``Decreto Legislativo``); codici without
  numero/anno print just the title.
- The act title prefers ``Act.subtitle`` (the descriptive h2 title; the h1
  holds only the estremi already covered by line 1) and falls back to
  ``Act.title``; markdown links and trailing GU redactional codes
  ("(18G00011)") are stripped.
- The rubrica drops its parenthesized source-reference tail
  ("(Articolo 3, commi ... legge 225/1992)") and markdown links.
- The pseudo/real article "unico" prints ``Articolo unico`` (never
  ``Art. unico``).
- Split chunks with numbered commi carry the comma range ("commi 100–120",
  en dash; "comma 250" when a single comma); split chunks with no numbered
  comma (paragraph fallback) carry "parte k/n".

Payload conventions
===================
- ``commi`` lists the numbers of the commi covered by the chunk (including
  the overlap comma). Unnumbered text (``Comma.number is None``, historical
  acts) contributes NO entry: a chunk made only of unnumbered text has
  ``commi == []`` and citations fall back to the article level.
- ``article`` is the bare normalized number ("18", "613-bis", "unico").
- ``id`` is ``{act_ref}#art-{number}#{i}`` with ``i`` the 0-based split
  index. When the same article number occurs more than once in an act (the
  approval decree art. 1 vs the attached code's art. 1, A4.3/A1) the 2nd+
  occurrences -- in document order -- use ``art-{number}.{occurrence}``
  (``rd-262-1942#art-1.2#0``), keeping ids unique act-wide. Ids are
  deterministic: same input, same ids.

Body text is kept verbatim (the URN links feed E4 citation-following; comma
text keeps the leading "N. " so chunks read naturally) except that runs of
3+ newlines are collapsed to one blank line. AGGIORNAMENTO blocks stay in
the last comma's text (A8): they describe the consolidation history of the
very text in the chunk.
"""

import re

from pydantic import BaseModel

from legger.corpus.models import Act, Article, Vigenza
from legger.corpus.refs import ActRef

# Split thresholds and size budgets (chars). MAX_TEXT >= MAX_HEADER + 2 +
# MAX_BODY by construction, so the cap is enforced structurally.
SPLIT_MAX_COMMI = 25
SPLIT_MAX_BODY = 6000
TARGET_BODY = 4500
MAX_BODY = 7600
MAX_HEADER = 300
MAX_TEXT = 8000

_SEP = "\n\n"

# act_type slug -> humanized "tipo esteso" for the header. Unknown slugs
# fall back to a title-cased slug.
_ACT_TYPE_LABELS = {
    "legge": "Legge",
    "legge_costituzionale": "Legge Costituzionale",
    "decreto_legge": "Decreto-Legge",
    "decreto_legislativo": "Decreto Legislativo",
    "decreto_legislativo_luogotenenziale": "Decreto Legislativo Luogotenenziale",
    "decreto_legge_luogotenenziale": "Decreto-Legge Luogotenenziale",
    "decreto_luogotenenziale": "Decreto Luogotenenziale",
    "decreto_legislativo_cps": "Decreto Legislativo del Capo Provvisorio dello Stato",
    "regio_decreto": "Regio Decreto",
    "regio_decreto_legge": "Regio Decreto-Legge",
    "regio_decreto_legislativo": "Regio Decreto Legislativo",
    "dpr": "D.P.R.",
    "dpcm": "D.P.C.M.",
    "decreto_ministeriale": "Decreto Ministeriale",
    "decreto": "Decreto",
    "codice": "Codice",
    "testo_unico": "Testo Unico",
    "atto_normativo": "Atto normativo",
}

_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
# GU redactional code in parentheses: "(18G00011)", "(042U0262)", "(0600226R)";
# plus the "(Raccolta 2018)" tail of some h1 titles.
_GU_CODE = re.compile(r"\s*\(\s*(?:\d{2,4}[A-Z]\d{4,5}[A-Z]?|\d{6,7}[A-Z]|Raccolta \d{4})\s*\)")
# Parenthesized source-reference tail of ATX rubriche: "(Articolo 3, ...)".
_RUBRIC_SOURCE = re.compile(r"\s*\((?:Articolo|Artt?\.)\s.*$", re.DOTALL)
_BLANK_RUN = re.compile(r"\n{3,}")
_PARA_SPLIT = re.compile(r"\n\s*\n")


class Chunk(BaseModel):
    """The retrieval unit: §4.2 payload fields plus header and text."""

    id: str
    act_ref: str
    act_type: str
    act_title: str | None = None
    article: str
    commi: list[str] = []
    collection: str
    vigenza: Vigenza
    file_path: str
    header: str
    text: str


# A segment is the grouping unit: a comma, or a piece of an oversized comma
# (the piece keeps the comma's label). ``label`` is None for unnumbered text.
_Segment = tuple[str | None, str]


def chunk_act(
    act: Act,
    ref: ActRef,
    *,
    vigenza: Vigenza,
    collection: str,
    file_path: str,
) -> list[Chunk]:
    """Chunk a parsed act into retrieval units (deterministic, never raises)."""
    act_title = _act_title(act)
    chunks: list[Chunk] = []
    occurrences: dict[str, int] = {}
    for article in act.articles:
        occ = occurrences.get(article.number, 0) + 1
        occurrences[article.number] = occ
        # ".{occ}" disambiguator: assumes B3's number normalization (suffixes
        # in hyphen form, e.g. "1-bis") makes a literal "1.2" article number
        # improbable, so the suffix cannot collide with a real number.
        art_key = article.number if occ == 1 else f"{article.number}.{occ}"
        groups = _split_article(article)
        total = len(groups)
        for i, group in enumerate(groups):
            commi = list(dict.fromkeys(label for label, _ in group if label is not None))
            marker = _range_marker(commi, i, total) if total > 1 else None
            header = _build_header(ref, act_title, article, marker)
            body = _SEP.join(text for _, text in group)
            text = f"{header}{_SEP}{body}" if body else header
            chunks.append(
                Chunk(
                    id=f"{ref.act_ref}#art-{art_key}#{i}",
                    act_ref=ref.act_ref,
                    act_type=ref.act_type,
                    act_title=act_title,
                    article=article.number,
                    commi=commi,
                    collection=collection,
                    vigenza=vigenza,
                    file_path=file_path,
                    header=header,
                    text=text,
                )
            )
    return chunks


# ---------------------------------------------------------------------------
# Splitting: article -> groups of segments
# ---------------------------------------------------------------------------


def _split_article(article: Article) -> list[list[_Segment]]:
    segments: list[_Segment] = []
    for comma in article.commi:
        text = _BLANK_RUN.sub(_SEP, comma.text).strip()
        if text:
            segments.append((comma.number, text))
    if not segments:
        return [[]]
    # Deliberate asymmetry: the commi-count check uses the raw article.commi
    # (the structural count, including commi whose text normalized to blank),
    # while body_len measures only the surviving non-empty segments -- the
    # text that actually lands in the chunk.
    body_len = sum(len(text) for _, text in segments) + 2 * (len(segments) - 1)
    if len(article.commi) <= SPLIT_MAX_COMMI and body_len <= SPLIT_MAX_BODY:
        return [segments]
    pieces: list[_Segment] = []
    for label, text in segments:
        if len(text) > MAX_BODY:
            pieces.extend((label, piece) for piece in _split_oversized(text))
        else:
            pieces.append((label, text))
    return _group(pieces)


def _split_oversized(text: str) -> list[str]:
    """Split a > MAX_BODY text at paragraph, then line, then char boundaries.

    Each unit carries the separator it had in the source (blank line at
    paragraph boundaries, single newline at line boundaries) so the rejoin in
    :func:`_merge_units` never inflates single newlines into blank lines.
    """
    paragraphs = [p for p in _PARA_SPLIT.split(text) if p.strip()]
    units: list[tuple[str, str]] = []  # (separator before the unit, unit)
    for paragraph in paragraphs:
        if len(paragraph) <= MAX_BODY:
            units.append((_SEP, paragraph))
            continue
        # Paragraph fallback failed: line boundaries.
        for k, line in enumerate(paragraph.splitlines()):
            sep = _SEP if k == 0 else "\n"
            if len(line) <= MAX_BODY:
                units.append((sep, line))
            else:
                # Last resort: hard slices (a single line longer than the
                # body budget would otherwise break the cap).
                units.extend(
                    (sep if start == 0 else "\n", line[start : start + TARGET_BODY])
                    for start in range(0, len(line), TARGET_BODY)
                )
    return _merge_units(units)


def _merge_units(units: list[tuple[str, str]]) -> list[str]:
    """Greedily merge consecutive units into pieces of <= TARGET_BODY chars.

    Each unit is rejoined with the separator it had in the source text (the
    first unit of a piece needs none).
    """
    pieces: list[str] = []
    bucket = ""
    for sep, unit in units:
        if not bucket:
            bucket = unit
        elif len(bucket) + len(sep) + len(unit) <= TARGET_BODY:
            bucket += sep + unit
        else:
            pieces.append(bucket)
            bucket = unit
    if bucket:
        pieces.append(bucket)
    return pieces


def _group(segments: list[_Segment]) -> list[list[_Segment]]:
    """Greedy comma groups with an overlap of exactly 1 segment.

    Each group is filled up to TARGET_BODY; a group may always take a second
    segment up to MAX_BODY (so an oversized boundary comma never strands a
    duplicate single-segment chunk). The next group re-starts at the last
    segment of the previous one (the 1-comma overlap) unless that pair would
    itself exceed MAX_BODY.
    """
    groups: list[list[_Segment]] = []
    i = 0
    n = len(segments)
    while i < n:
        size = len(segments[i][1])
        j = i + 1
        while j < n:
            grown = size + len(_SEP) + len(segments[j][1])
            if grown <= TARGET_BODY or (j == i + 1 and grown <= MAX_BODY):
                size = grown
                j += 1
            else:
                break
        groups.append(segments[i:j])
        if j >= n:
            break
        pair = len(segments[j - 1][1]) + len(_SEP) + len(segments[j][1])
        i = j - 1 if pair <= MAX_BODY else j
    return groups


def _range_marker(commi: list[str], index: int, total: int) -> str:
    if not commi:
        return f"parte {index + 1}/{total}"
    if len(commi) == 1:
        return f"comma {commi[0]}"
    return f"commi {commi[0]}–{commi[-1]}"


# ---------------------------------------------------------------------------
# Contextual header
# ---------------------------------------------------------------------------


def _build_header(ref: ActRef, act_title: str | None, article: Article, marker: str | None) -> str:
    """Assemble the header, shrinking piece budgets until <= MAX_HEADER."""
    for budgets in ((120, 90, 60), (80, 70, 50), (60, 50, 40), (40, 40, 30)):
        header = _assemble_header(ref, act_title, article, marker, *budgets)
        if len(header) <= MAX_HEADER:
            return header
    # Backstop: the smallest budgets are sized to fit, but a pathological
    # input must never break the structural MAX_TEXT cap.
    return header[:MAX_HEADER]


def _assemble_header(
    ref: ActRef,
    act_title: str | None,
    article: Article,
    marker: str | None,
    title_budget: int,
    rubric_budget: int,
    path_budget: int,
) -> str:
    label = _ACT_TYPE_LABELS.get(ref.act_type) or ref.act_type.replace("_", " ").title()
    title = _truncate(act_title, title_budget) if act_title else None
    if ref.number and ref.year:
        estremi = f"{label} {ref.number}/{ref.year}"
        line1 = f"{estremi} — {title}" if title else estremi
    else:
        # Codici without numero/anno: just the title.
        line1 = title or label

    lines = [line1]
    if article.path:
        lines.append(_truncate(" — ".join(article.path), path_budget))

    # "unico" is the markdown pseudo-article number; the AKN path can emit
    # the raw "Articolo unico" heading text as the number (failure contract).
    if article.number == "unico" or article.number.lower().startswith("articolo unico"):
        art_line = "Articolo unico"
    else:
        # The AKN parser's failure contract can emit an arbitrary-length raw
        # heading as the number: budget its contribution like the rubrica so
        # the header (and thus MAX_TEXT) stays structurally capped.
        art_line = f"Art. {_truncate(article.number, rubric_budget)}"
    rubrica = _clean_rubrica(article.heading)
    if rubrica:
        art_line += f" — {_truncate(rubrica, rubric_budget)}"
    if marker:
        art_line += f" ({marker})"
    lines.append(art_line)
    return "\n".join(lines)


def _act_title(act: Act) -> str | None:
    """Descriptive act title: the h2 subtitle, falling back to the h1."""
    for raw in (act.subtitle, act.title):
        if not raw:
            continue
        text = _MD_LINK.sub(r"\1", raw)
        text = _GU_CODE.sub("", text)
        text = " ".join(text.split())
        if text:
            return text
    return None


def _clean_rubrica(heading: str | None) -> str | None:
    if not heading:
        return None
    text = _MD_LINK.sub(r"\1", heading)
    text = _RUBRIC_SOURCE.sub("", text)
    text = " ".join(text.split()).strip()
    return text or None


def _truncate(text: str, budget: int) -> str:
    if len(text) <= budget:
        return text
    cut = text[: budget - 1]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip() + "…"
