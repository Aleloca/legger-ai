"""Fast path for explicit normative references (Task E1).

When the user cites a norm by its estremi ("art. 256 del d.lgs. 152/2006")
or by a code name/abbreviation ("art. 274 c.p.p."), embedding similarity is
the wrong tool: the reference is exact, so the chunks can be fetched directly
from Qdrant by payload filter. This module provides the two halves:

- :func:`extract_refs` — pure text -> :class:`ExtractedRef` list, NO I/O.
  Precision >> recall: a false extraction sends garbage chunks to the LLM,
  while a missed one just falls back to hybrid search (cheap). Every
  ambiguity below is resolved in favor of NOT extracting.
- :func:`resolve_refs` — refs -> :class:`~legger.retrieval.search.SearchHit`
  list via Qdrant ``scroll`` payload filters (synthetic ``score=1.0``).
  Misses return nothing; the caller (E5 pipeline) falls back to hybrid.

Grammar decisions (the test table in ``tests/test_fastpath.py`` is the
contract):

- **Short abbreviations require a bound article**: the SHORT dotted forms
  (c.c., c.p., c.p.c., c.p.p., c.d.s., cds — ≤ ~6 chars) collide with
  everyday abbreviations ("c.c." conto corrente, "c.p." casella postale,
  "CdS" Consiglio di Stato), so they emit a ref ONLY when an article is
  adjacent ("art. 2051 c.c."); bare they are ignored. FULL names ("codice
  civile", "codice della strada") are unambiguous in prose and may still
  emit act-level refs without an article.
- **Codici abbreviations** map to the act_refs ACTUALLY in the corpus, not
  to registry slugs that never fire: the c.p.p. is generational so its
  act_ref is the carrier ``dpr-447-1988``; the CdS (dlgs 285/1992) is
  subtitled "Nuovo codice della strada" which never anchors the registry,
  so ``codice della strada`` -> ``dlgs-285-1992``. See
  :data:`_REGISTRY_ACT_REF_OVERRIDES` (audited against the complete
  ``Codici`` collection, 40 acts, 2026-06).
- **Costituzione** (``cost.``/``costituzione``) is recognized but NOT in the
  corpus: a bound article ("art. 32 Cost.") is dropped ENTIRELY rather than
  emitted with ``act_ref=None`` — an act_ref-None ref invites the caller to
  bind the article to whatever act the chat context holds, which here would
  always be wrong.
- **Adjacency**: an article and its source must be adjacent-ish — the gap
  may only contain punctuation and connectives ("del", "della", "di", a
  comma clause). An article with no source nearby is emitted with
  ``act_ref=None`` (the chat context may bind it later; E5 decides).
- **``c.`` ambiguity**: ``c. 1`` (digit follows) is a comma; ``c.c.``
  (letter follows) is a codice. ``comma``/``co.`` are always commi.
- **Commi** annotate the ref but do NOT narrow the fetch: the LLM wants the
  whole article as context; the comma stays available to the caller.
- Generational codici bind the bare name to the VIGENTE carrier (e.g.
  "codice dei contratti pubblici" -> ``dlgs-36-2023``), per the refs.py
  doctrine that the bare name means "the codice vigente".
- Out of scope (documented misses, hybrid fallback covers them): prose
  estremi with dates ("decreto legislativo 9 aprile 2008, n. 81" — Task E4),
  leggi costituzionali, two-digit years ("81/08"), dotless code
  abbreviations ("cc", "cpp" — too ambiguous in chat text).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel
from qdrant_client import QdrantClient, models
from sqlalchemy import Engine, select

from legger.corpus._common import SUFFIX_ALT, SUFFIX_RANK
from legger.corpus.refs import _KNOWN_CODICI
from legger.db import acts
from legger.retrieval.search import SearchHit

# Letters as seen by the boundary guards (plain + accented lowercase; the
# regexes run with IGNORECASE so the uppercase variants are covered too).
_L = "a-zà-ù"

logger = logging.getLogger(__name__)


class ExtractedRef(BaseModel):
    """One normative reference extracted from free text (no I/O involved).

    ``act_ref`` is the canonical slug when determinable without a database
    (code abbreviations/names and tipo+numero+anno estremi); ``None`` for an
    article cited without a source nearby. ``act_type``/``number``/``year``
    are filled only for estremi-derived refs — their presence signals that
    the act_ref was COMPUTED (not corpus-verified) and should be checked
    against the ``acts`` table before fetching.
    """

    act_ref: str | None = None
    act_type: str | None = None
    number: str | None = None
    year: int | None = None
    article: str | None = None
    comma: str | None = None


# ---------------------------------------------------------------------------
# Codici: name/abbreviation -> corpus act_ref
# ---------------------------------------------------------------------------

# Registry slugs that are NOT the corpus act_ref. Two classes:
# - generational codici (refs.py never lets the registry slug override the
#   carrier header): mapped to the VIGENTE generation's carrier act_ref;
# - codici whose title never anchors the registry key (CdS "Nuovo codice
#   della strada", processo amministrativo, antimafia): mapped to their
#   header-derived act_ref.
# Audited against the complete `Codici` Qdrant collection (40 act_refs).
_REGISTRY_ACT_REF_OVERRIDES: dict[str, str] = {
    "codice-procedura-penale": "dpr-447-1988",
    "codice-strada": "dlgs-285-1992",
    "codice-navigazione": "rd-327-1942",
    "codice-contratti-pubblici": "dlgs-36-2023",
    "codice-postale-telecomunicazioni": "dpr-156-1973",
    "codice-processo-amministrativo": "dlgs-104-2010",
    "codice-antimafia": "dlgs-159-2011",
}

# Common names absent from the refs.py registry (normalized like its keys:
# lowercase, accents stripped, apostrophes as spaces).
_EXTRA_CODICI_NAMES: dict[str, str] = {
    "codice privacy": "codice-privacy",
    "codice della privacy": "codice-privacy",
    "codice dell ambiente": "dlgs-152-2006",
    "nuovo codice della strada": "dlgs-285-1992",
    "codice di giustizia contabile": "dlgs-174-2016",
    "codice delle pari opportunita": "dlgs-198-2006",
    "codice del turismo": "dlgs-79-2011",
}

_CODICI_BY_NAME: dict[str, str] = {
    **{name: _REGISTRY_ACT_REF_OVERRIDES.get(slug, slug) for name, slug in _KNOWN_CODICI},
    **_EXTRA_CODICI_NAMES,
}

# Dotted abbreviations, longest-first so "c.p.c." never half-matches as
# "c.p.". The final dot is optional (end-of-message citations); the dotless
# short forms are deliberately NOT supported except "cds" (common in traffic
# questions — but like all SHORT forms it is ambiguous on its own: "il CdS"
# is usually the Consiglio di Stato). The third field is `requires_article`:
# short forms (≤ ~6 chars) collide with everyday abbreviations ("c.c." conto
# corrente, "c.p." casella postale) and act as a source ONLY when an article
# binds to them; the spelled-out `cod. xxx` forms stand on their own.
_ABBREVIATIONS: tuple[tuple[str, str, bool], ...] = (
    (r"c\.\s*p\.\s*c\.?", "codice-procedura-civile", True),
    (r"c\.\s*p\.\s*p\.?", "dpr-447-1988", True),
    (r"c\.\s*d\.\s*s\.?", "dlgs-285-1992", True),
    (r"cds", "dlgs-285-1992", True),
    (r"cod\.\s*proc\.\s*civ\.?", "codice-procedura-civile", False),
    (r"cod\.\s*proc\.\s*pen\.?", "dpr-447-1988", False),
    (r"cod\.\s*civ\.?", "codice-civile", False),
    (r"cod\.\s*pen\.?", "codice-penale", False),
    (r"cod\.\s*nav\.?", "rd-327-1942", False),
    (r"c\.\s*c\.?", "codice-civile", True),
    (r"c\.\s*p\.?", "codice-penale", True),
)

_ACCENT_CLASS = {"a": "[aàá]", "e": "[eèé]", "i": "[iìí]", "o": "[oòó]", "u": "[uùú]"}


def _name_pattern(name: str) -> str:
    """Registry key -> regex: accent-tolerant words, flexible separators.

    Keys are normalized ("codice dell ordinamento militare"), queries are not
    ("codice dell'ordinamento militare"): word gaps match spaces, apostrophes
    and hyphens; vowels match their accented variants ("proprieta" matches
    "proprietà").
    """
    words = ["".join(_ACCENT_CLASS.get(ch, re.escape(ch)) for ch in word) for word in name.split()]
    return r"[\s'’\-]+".join(words)


# An abbreviation followed by more single-letter-dot units is a LONGER
# abbreviation we do not know ("c.c.n.l." must not be read as "c.c."): the
# lookahead rejects a continuation like ".n." / "n." whether or not the
# final optional dot of the pattern was consumed.
_NO_ABBREV_CONTINUATION = rf"(?!\.?\s*[{_L}]\.)"


def _compile(pattern: str, *, guard: str = "") -> re.Pattern[str]:
    return re.compile(rf"(?<![{_L}.])(?:{pattern})(?![{_L}]){guard}", re.IGNORECASE)


# A codice name followed by a nationality adjective is a FOREIGN code
# ("art. 242 del codice civile tedesco" is the BGB): not in the corpus,
# do not extract.
_NATIONALITY_GUARD = (
    rf"(?!\s+(?:tedesc|frances|svizzer|austriac|spagnol|europe|ingles|"
    rf"american|statunitens|olandes|portoghes|belg|grec)[{_L}]*)"
)

# Longest name first: "codice penale militare di pace" must shadow "codice
# penale", "nuovo codice della strada" must shadow "codice della strada".
# Full names are unambiguous in prose: requires_article=False.
_CODICE_NAME_MATCHERS: tuple[tuple[re.Pattern[str], str, bool], ...] = tuple(
    (_compile(_name_pattern(name), guard=_NATIONALITY_GUARD), act_ref, False)
    for name, act_ref in sorted(_CODICI_BY_NAME.items(), key=lambda kv: len(kv[0]), reverse=True)
)

_ABBREVIATION_MATCHERS: tuple[tuple[re.Pattern[str], str, bool], ...] = tuple(
    (_compile(pattern, guard=_NO_ABBREV_CONTINUATION), act_ref, requires_article)
    for pattern, act_ref, requires_article in _ABBREVIATIONS
)

# Recognized but NOT in the corpus (the "Leggi costituzionali" collection
# holds only revision laws): swallows the reference, see module docstring.
_COSTITUZIONE_RE = _compile(r"cost\.|costituzione", guard=_NO_ABBREV_CONTINUATION)

# ---------------------------------------------------------------------------
# Estremi: tipo + numero + anno
# ---------------------------------------------------------------------------

# Group name -> (act_type, act_ref prefix), aligned with refs._REF_PREFIX.
_ESTREMI_TYPES: dict[str, tuple[str, str]] = {
    "dlgs": ("decreto_legislativo", "dlgs"),
    "dpcm": ("dpcm", "dpcm"),
    "dpr": ("dpr", "dpr"),
    "dl": ("decreto_legge", "dl"),
    "rd": ("regio_decreto", "rd"),
    "legge": ("legge", "legge"),
}

# The tipo token must be IMMEDIATELY followed by numero+anno ("81/2008",
# "n. 81 del 2008"): "il decreto legislativo è una fonte..." never fires.
# `d.l.` carries a (?!gs) guard so it can never eat the head of "d.lgs.";
# `l.` carries lookbehinds rejecting a letter-dot prefix, so the tail of a
# longer abbreviation never reads as legge ("s.r.l. 104/2020" is a company,
# mirror of _NO_ABBREV_CONTINUATION); the year is 4-digit by design
# (precision: "90/2000" decades, "81/08").
_ESTREMI_RE = re.compile(
    rf"(?<![{_L}])"
    rf"(?:"
    rf"(?P<dlgs>d\.?\s*lgs\.?(?![{_L}])|decreto\s+legislativo(?![{_L}]))"
    rf"|(?P<dpcm>d\.\s*p\.\s*c\.\s*m\.?(?![{_L}])|dpcm(?![{_L}]))"
    rf"|(?P<dpr>d\.\s*p\.\s*r\.?(?![{_L}])|dpr(?![{_L}])"
    rf"|decreto\s+del\s+presidente\s+della\s+repubblica(?![{_L}]))"
    rf"|(?P<dl>d\.\s*l\.(?!\s*gs)|dl\.?(?![{_L}])|decreto[\s\-]+legge(?![{_L}]))"
    rf"|(?P<rd>r\.\s*d\.?(?![{_L}])|rd(?![{_L}])|regio\s+decreto(?![{_L}]))"
    rf"|(?P<legge>legge(?![{_L}])|(?<![{_L}]\.)(?<![{_L}]\.\s)l\.)"
    rf")"
    rf"\s*,?\s*(?:n\.?|n°|num\.?|numero)?\s*"
    rf"(?P<num>\d{{1,4}}(?:[\s\-](?:{SUFFIX_ALT})(?![{_L}]))?)"
    rf"\s*(?:/\s*|\bdel\s+)"
    rf"(?P<year>1[89]\d\d|20\d\d)(?!\d)",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Articles and commi
# ---------------------------------------------------------------------------

_NUM = rf"\d+(?:\s*/\s*\d+)?(?:[\s.\-]+(?:{SUFFIX_ALT})(?![{_L}]))?"
# "c." opens a comma only when a digit follows — that is what disambiguates
# "art. 18, c. 1" (comma) from "art. 18 c.c." (codice). The ordinal form
# ("art. 1341, secondo comma, c.c.") is the classic civil-law style.
_ORDINALI = {
    "primo": "1",
    "secondo": "2",
    "terzo": "3",
    "quarto": "4",
    "quinto": "5",
    "sesto": "6",
    "settimo": "7",
    "ottavo": "8",
    "nono": "9",
    "decimo": "10",
}
_COMMA_CLAUSE = (
    rf"(?:\s*,)?\s*(?:"
    rf"(?:comm[ai](?![{_L}])|co\.|c\.(?=\s*\d))\s*"
    rf"(?P<comma>\d+(?:[\s.\-]+(?:{SUFFIX_ALT})(?![{_L}]))?)"
    rf"|(?P<ordinale>{'|'.join(_ORDINALI)})\s+comma(?![{_L}])"
    rf")"
)
# "e <num>" list continuations are greedy traps after a SINGULAR keyword:
# "art. 18 e 7 giorni dopo" / "tra l'art. 18 e 2000 euro" must not bind the
# second number. A plural keyword ("artt.", "articoli") announces a list, so
# the continuation is always accepted; after singular "art." the continuation
# number must look like a citation tail — carry a latin suffix ("e 2-bis"),
# or be followed by a connective/comma/codice token or end punctuation
# ("artt. 2043 e 2051 c.c.", "art. 16 e 17 del d.lgs. ...", "... e 2051?").
_NUM_SUFFIXED = rf"\d+(?:\s*/\s*\d+)?[\s.\-]+(?:{SUFFIX_ALT})(?![{_L}])"
_E_CONT_GUARD = (
    rf"(?=\s*(?:$|[,;.:)\]?!»\"']"
    rf"|(?:del|dello|della|dei|delle|degli|di|al|allo|alla|ai|nel|nella)(?![{_L}])"
    rf"|(?:d|all|dell|nell)['’]"
    rf"|comm[ai](?![{_L}])|co\.|c\.|cod\.|codice(?![{_L}])"
    rf"))"
)
_E_CONT = rf"(?:{_NUM_SUFFIXED}|\d+(?:\s*/\s*\d+)?{_E_CONT_GUARD})"
_ARTICLE_RE = re.compile(
    rf"(?<![{_L}])(?:"
    rf"(?:articoli|artt)\.?\s*(?P<nums_pl>{_NUM}(?:(?:\s*[,;]\s*|\s+ed?\s+){_NUM})*)"
    rf"|(?:articolo|art)\.?\s*(?P<nums_sg>{_NUM}(?:\s*[,;]\s*{_NUM}|\s+ed?\s+{_E_CONT})*)"
    rf")(?:{_COMMA_CLAUSE})?",
    re.IGNORECASE,
)
_NUM_LIST_SEP = re.compile(r"\s*[,;]\s*|\s+ed?\s+", re.IGNORECASE)

# Article -> source gap: punctuation plus connectives only ("art. 18, comma
# 1, del d.lgs. ..." — the comma clause is already part of the article
# match). The "e <num>" token covers commi lists, whose tail stays in the
# gap ("art. 1, commi 2 e 3, c.c.").
_FORWARD_GAP = re.compile(
    r"^[\s,;]*(?:(?:del|dello|della|dei|delle|degli|di|al|allo|alla|ai|nel|nella)\s+"
    r"|(?:d|all|dell|nell)['’]\s*"
    r"|ed?\s+\d+[\w/\-]*[\s,;]*)*$",
    re.IGNORECASE,
)
# Source -> article gap ("d.lgs. 81/2008 art 18", "..., all'art. 18").
_BACKWARD_GAP = re.compile(r"^[\s,;:]*(?:all['’])?\s*$", re.IGNORECASE)
# Comma clause spelled AFTER the source: "art. 2051 c.c., comma 1". Only the
# unambiguous spellings here (a bare "c." after a source is too risky).
_TRAILING_COMMA_RE = re.compile(
    rf"^(?:\s*,)?\s*(?:comm[ai](?![{_L}])|co\.)\s*"
    rf"(\d+(?:[\s.\-]+(?:{SUFFIX_ALT})(?![{_L}]))?)",
    re.IGNORECASE,
)


def _norm_number(raw: str) -> str:
    """Normalize an article/comma number: "613 bis" -> "613-bis", "314 / 2" -> "314/2"."""
    num = raw.lower().strip()
    num = re.sub(r"\s*/\s*", "/", num)
    return re.sub(rf"[\s.\-]+(?=(?:{SUFFIX_ALT})$)", "-", num)


@dataclass(frozen=True)
class _Source:
    start: int
    end: int
    act_ref: str | None
    act_type: str | None = None
    number: str | None = None
    year: int | None = None
    drop: bool = False  # recognized but unresolvable (Costituzione)
    requires_article: bool = False  # short abbreviation: no act-level ref


@dataclass(frozen=True)
class _ArticleMatch:
    start: int
    end: int
    numbers: tuple[str, ...]
    comma: str | None


def _find_articles(query: str) -> list[_ArticleMatch]:
    found = []
    for m in _ARTICLE_RE.finditer(query):
        nums = m.group("nums_pl") or m.group("nums_sg")
        numbers = tuple(_norm_number(part) for part in _NUM_LIST_SEP.split(nums) if part)
        comma = _norm_number(m.group("comma")) if m.group("comma") else None
        if comma is None and m.group("ordinale"):
            comma = _ORDINALI[m.group("ordinale").lower()]
        found.append(_ArticleMatch(m.start(), m.end(), numbers, comma))
    return found


def _find_sources(query: str) -> list[_Source]:
    candidates: list[_Source] = []
    for m in _ESTREMI_RE.finditer(query):
        group = next(name for name in _ESTREMI_TYPES if m.group(name) is not None)
        act_type, prefix = _ESTREMI_TYPES[group]
        number = _norm_number(m.group("num")).replace(" ", "-")
        year = int(m.group("year"))
        candidates.append(
            _Source(m.start(), m.end(), f"{prefix}-{number}-{year}", act_type, number, year)
        )
    for matchers in (_ABBREVIATION_MATCHERS, _CODICE_NAME_MATCHERS):
        for pattern, act_ref, requires_article in matchers:
            candidates.extend(
                _Source(m.start(), m.end(), act_ref, "codice", requires_article=requires_article)
                for m in pattern.finditer(query)
            )
    candidates.extend(
        _Source(m.start(), m.end(), None, drop=True) for m in _COSTITUZIONE_RE.finditer(query)
    )
    # Overlap resolution: leftmost start, then longest match wins ("c.p.c."
    # over "c.p.", "codice penale militare di pace" over "codice penale").
    candidates.sort(key=lambda s: (s.start, -(s.end - s.start)))
    kept: list[_Source] = []
    for src in candidates:
        if not kept or src.start >= kept[-1].end:
            kept.append(src)
    return kept


def _bind(
    art: _ArticleMatch, sources: list[_Source], used: set[int], query: str
) -> tuple[int | None, str | None]:
    """Bind an article to its adjacent source; returns (source index, trailing comma)."""
    after = [(i, s) for i, s in enumerate(sources) if s.start >= art.end]
    if after:
        i, src = min(after, key=lambda t: t[1].start)
        if i not in used and _FORWARD_GAP.fullmatch(query[art.end : src.start]):
            trailing = None
            if art.comma is None:
                m = _TRAILING_COMMA_RE.match(query[src.end :])
                if m:
                    trailing = _norm_number(m.group(1))
            return i, trailing
    before = [(i, s) for i, s in enumerate(sources) if s.end <= art.start]
    if before:
        # Reuse is allowed backwards: "d.lgs. 81/2008, art. 14, art. 18".
        i, src = max(before, key=lambda t: t[1].end)
        if _BACKWARD_GAP.fullmatch(query[src.end : art.start]):
            return i, None
    return None, None


def extract_refs(query: str) -> list[ExtractedRef]:
    """Extract explicit normative references from free text (pure, no I/O).

    Returns refs in textual order, deduplicated on (act_ref, article, comma).
    Articles with no adjacent source come back with ``act_ref=None`` (the
    caller may bind them from chat context); sources with no article become
    act-level refs (``article=None``). When one ``artt.`` lists several
    articles the comma is dropped (it cannot be attributed unambiguously).
    """
    if not query or not query.strip():
        return []
    articles = _find_articles(query)
    sources = _find_sources(query)
    used: set[int] = set()
    anchored: list[tuple[int, ExtractedRef]] = []
    for art in articles:
        idx, trailing_comma = _bind(art, sources, used, query)
        src = None
        if idx is not None:
            used.add(idx)
            src = sources[idx]
            if src.drop:
                continue  # e.g. "art. 32 Cost.": known act, not in corpus
        comma = art.comma if art.comma is not None else trailing_comma
        for number in art.numbers:
            anchored.append(
                (
                    art.start,
                    ExtractedRef(
                        act_ref=src.act_ref if src else None,
                        act_type=src.act_type if src else None,
                        number=src.number if src else None,
                        year=src.year if src else None,
                        article=number,
                        comma=comma if len(art.numbers) == 1 else None,
                    ),
                )
            )
    for i, src in enumerate(sources):
        if i in used or src.drop or src.requires_article:
            # requires_article: a bare short abbreviation ("ho un c.c. presso
            # la banca") is everyday language, not a citation — only an
            # adjacent article makes it one.
            continue
        anchored.append(
            (
                src.start,
                ExtractedRef(
                    act_ref=src.act_ref, act_type=src.act_type, number=src.number, year=src.year
                ),
            )
        )
    anchored.sort(key=lambda item: item[0])
    seen: set[tuple[str | None, str | None, str | None]] = set()
    refs: list[ExtractedRef] = []
    for _, ref in anchored:
        key = (ref.act_ref, ref.article, ref.comma)
        if key not in seen:
            seen.add(key)
            refs.append(ref)
    return refs


# ---------------------------------------------------------------------------
# Resolution: refs -> SearchHits (Qdrant scroll, optional Postgres check)
# ---------------------------------------------------------------------------

#: Max chunks returned for one article ref (an article rarely splits into
#: more than a handful of chunks; 12 keeps pathological splits bounded).
ARTICLE_CHUNK_CAP = 12
#: Max chunks for an act-level ref (no article): just the OPENING articles —
#: enough to ground "what is the d.lgs. 81/2008 about", not the whole act.
ACT_CHUNK_CAP = 5
# Scroll scan bound for act-level refs: scroll has no server-side ordering,
# so the first-articles selection sorts client-side over at most this many
# chunks. For acts larger than the bound (codice civile ~4.7k chunks) the
# scanned subset is arbitrary-but-deterministic (point-id order); acceptable
# for a ref that explicitly names no article.
_ACT_SCAN_LIMIT = 2048
_SCROLL_PAGE = 256


def resolve_refs(
    refs: list[ExtractedRef],
    *,
    qdrant_client: QdrantClient,
    collection: str,
    engine: Engine | None = None,
) -> list[SearchHit]:
    """Fetch the chunks for extracted refs; misses simply yield nothing.

    Per ref: ``act_ref=None`` is skipped (context binding is the caller's
    job); estremi-COMPUTED act_refs (``number``/``year`` set) are first
    verified against the Postgres ``acts`` table when ``engine`` is given
    (cheap primary-key probe, skips the Qdrant roundtrip for garbage slugs
    like ``legge-90-2000`` from "l. 90/2000" typos); then the chunks are
    scrolled from Qdrant by payload filter — ``act_ref`` plus ``article``
    when present. No vigenza filter: an explicit reference means "give me
    that act", current or not. Hits are synthetic (``score=1.0``), ordered
    by ref then by article/split order, deduplicated on chunk_id.
    """
    hits: list[SearchHit] = []
    seen: set[str] = set()
    for ref in refs:
        if ref.act_ref is None:
            continue
        if engine is not None and ref.year is not None and not _act_exists(engine, ref.act_ref):
            # NOTE: the probe assumes the Postgres `acts` table is a superset
            # of the Qdrant collection. During a PARTIAL bootstrap the table
            # may lag behind Qdrant, and this rejection converts would-be
            # fast-path hits into hybrid fallbacks (observed live with
            # dlgs-152-2006); the debug line makes the skew visible.
            logger.debug(
                "fast-path probe rejected %s: not in acts table (may still exist in Qdrant)",
                ref.act_ref,
            )
            continue
        for payload in _fetch_payloads(qdrant_client, collection, ref):
            chunk_id = payload.get("chunk_id", "")
            if chunk_id and chunk_id in seen:
                continue
            seen.add(chunk_id)
            hits.append(_payload_to_hit(payload))
    return hits


def _act_exists(engine: Engine, act_ref: str) -> bool:
    with engine.connect() as conn:
        stmt = select(acts.c.act_ref).where(acts.c.act_ref == act_ref)
        return conn.execute(stmt).first() is not None


def _fetch_payloads(
    client: QdrantClient, collection: str, ref: ExtractedRef
) -> list[dict[str, Any]]:
    must: list[models.FieldCondition] = [
        models.FieldCondition(key="act_ref", match=models.MatchValue(value=ref.act_ref))
    ]
    if ref.article is not None:
        must.append(
            models.FieldCondition(key="article", match=models.MatchValue(value=ref.article))
        )
    scan_cap = 64 if ref.article is not None else _ACT_SCAN_LIMIT
    payloads: list[dict[str, Any]] = []
    offset = None
    while len(payloads) < scan_cap:
        records, offset = client.scroll(
            collection_name=collection,
            scroll_filter=models.Filter(must=must),
            limit=min(_SCROLL_PAGE, scan_cap - len(payloads)),
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        payloads.extend(record.payload or {} for record in records)
        if offset is None:
            break
    if ref.article is not None:
        payloads.sort(key=_split_index)
        return payloads[:ARTICLE_CHUNK_CAP]
    payloads.sort(key=lambda p: (_article_sort_key(p.get("article", "")), _split_index(p)))
    return payloads[:ACT_CHUNK_CAP]


def _split_index(payload: dict[str, Any]) -> int:
    """0-based split index from the chunk id (``act#art-N#i``); defensive."""
    tail = str(payload.get("chunk_id", "")).rsplit("#", 1)[-1]
    return int(tail) if tail.isdigit() else 0


def _article_sort_key(article: str) -> tuple[int, int, int, str]:
    """Legal article order: numeric base, then latin suffix rank ("2" < "2-bis" < "3").

    Non-numeric articles ("unico", annexes) sort last; the raw string breaks
    the remaining ties deterministically.
    """
    m = re.match(r"^(\d+)(?:-(\w+))?", article)
    if m is None:
        return (1, 0, 0, article)
    suffix_rank = SUFFIX_RANK.get(m.group(2), len(SUFFIX_RANK) + 1) if m.group(2) else 0
    return (0, int(m.group(1)), suffix_rank, article)


def _payload_to_hit(payload: dict[str, Any]) -> SearchHit:
    """Payload -> SearchHit with a synthetic perfect score (exact reference)."""
    return SearchHit(
        score=1.0,
        chunk_id=payload.get("chunk_id", ""),
        act_ref=payload.get("act_ref", ""),
        article=payload.get("article", ""),
        act_title=payload.get("act_title", ""),
        header=payload.get("header", ""),
        text=payload.get("text", ""),
        vigenza=payload.get("vigenza", ""),
        payload=payload,
    )
