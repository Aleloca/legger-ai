"""1-hop citation following over retrieval hits (Task E4).

Norm text is full of rinvii ("di cui all'articolo 14 del decreto legislativo
9 aprile 2008, n. 81", "ai sensi dell'art. 1341 del codice civile", "di cui
al comma 2"): when a retrieved chunk leans on another provision, the LLM
answers better with that provision in context. This module follows those
citations EXACTLY ONE hop:

- :func:`extract_prose_refs` — pure text -> :class:`ExtractedRef` list, NO
  I/O. Extends the E1 chat-query grammar (:mod:`legger.retrieval.fastpath`)
  to the forms found inside norm text. Two channels, URN first:

  1. **URN NIR links.** The corpus markdown keeps the Normattiva links
     (``[anchor](...urn:nir:stato:decreto.legislativo:2008-04-09;81~art14)``)
     and they survive chunking, so they are machine-precise and PREFERRED:
     when a link's URN parses, the ref comes from the URN and the anchor
     prose is consumed (never re-parsed — anchor and URN can disagree on
     details like comma lists, and the URN wins). Links whose URN does not
     parse (Costituzione URNs carry no numero, unknown codici) fall back to
     their anchor text; the URL itself never reaches the prose grammar.
  2. **Prose.** Date estremi ("<tipo> <D month YYYY>, n. <N>" -> slug
     ``<tipo>-<N>-<YYYY>``, the E1-documented miss) are normalized to the
     "<tipo> <N>/<YYYY>" form and the E1 grammar does the rest (article
     binding, codici names, adjacency); plus the internal "di cui al comma
     N" (act_ref=None AND article=None — the citing article's own comma).

  Ordering: URN refs first (in textual order), then prose refs (in textual
  order of the link-stripped text); deduplicated on (act_ref, article,
  comma) like :func:`~legger.retrieval.fastpath.extract_refs`.

- :func:`follow_citations` — hits -> NEW hits only (the caller appends).
  Internal refs (act_ref=None) bind to the citing hit's own act (and, for
  comma-only refs, its own article); refs whose act/article is already
  among the hits are dropped; the rest resolve via
  :func:`~legger.retrieval.fastpath.resolve_refs` in citation-frequency
  order (most-cited first) under a hard token budget. The returned hits are
  NOT re-followed: one hop, by design — citation graphs in the corpus are
  dense enough that hop 2 would routinely blow any budget with provisions
  only transitively related to the question.
"""

from __future__ import annotations

import re

from qdrant_client import QdrantClient
from sqlalchemy import Engine

from legger.corpus._common import SUFFIX_ALT
from legger.corpus.refs import _MONTHS, act_slugs
from legger.retrieval.fastpath import (
    _CODICI_BY_NAME,
    _ESTREMI_TIPO,
    _L,
    ExtractedRef,
    _norm_number,
    extract_refs,
    resolve_refs,
)
from legger.retrieval.search import SearchHit

# ---------------------------------------------------------------------------
# URN NIR links
# ---------------------------------------------------------------------------

# Markdown link as the corpus prints it: [anchor](url). The anchor never
# nests brackets; the URL never contains spaces or a closing paren.
_MD_LINK_RE = re.compile(r"\[([^\]\n]*)\]\(([^()\s]+)\)")

# urn:nir:stato:<tipo>:<date>;<num>[~art<N>[-com<M>]]. The date may be the
# year alone (urn:nir:stato:legge:1990;241); the article number glues its
# latin suffix without a separator ("art1bis"); trailing URN qualifiers
# (-let.., !vig, the closing paren of the markdown link) end the match.
# Only the stato authority: the rare presidente.repubblica URNs are covered
# by their anchor prose falling through to the date-estremi grammar.
_URN_RE = re.compile(
    r"urn:nir:stato:(?P<tipo>[a-z][a-z.]*):"
    r"(?P<year>\d{4})(?:-\d{2}-\d{2})?;"
    r"(?P<num>\d+(?:-[a-z]+)?)"
    rf"(?:~art(?P<art>\d+(?:{SUFFIX_ALT})?)(?:-com(?P<comma>\d+(?:{SUFFIX_ALT})?))?)?",
    re.IGNORECASE,
)

# "art1bis" / "com2ter": split the glued latin suffix back out.
_GLUED_NUM_RE = re.compile(rf"^(\d+)({SUFFIX_ALT})?$", re.IGNORECASE)


def _norm_glued(num: str) -> str:
    m = _GLUED_NUM_RE.match(num)
    if m is None:  # defensive: _URN_RE only produces glued shapes
        return num.lower()
    return f"{m.group(1)}-{m.group(2).lower()}" if m.group(2) else m.group(1)


def _ref_from_urn(url: str) -> ExtractedRef | None:
    """URN URL -> ref, or None when it does not name a fetchable act."""
    m = _URN_RE.search(url)
    if m is None:
        return None
    tipo = m.group("tipo").lower().replace(".", " ")
    article = _norm_glued(m.group("art")) if m.group("art") else None
    comma = _norm_glued(m.group("comma")) if m.group("comma") else None
    if tipo.startswith("codice"):
        # Through the same registry as E1 ("codice penale" -> codice-penale,
        # generational carriers included); unknown codici are NOT emitted —
        # the caller keeps the anchor prose as the fallback channel.
        act_ref = _CODICI_BY_NAME.get(tipo)
        if act_ref is None:
            return None
        return ExtractedRef(act_ref=act_ref, act_type="codice", article=article, comma=comma)
    if tipo == "costituzione":
        return None  # recognized but not in the corpus (E1 doctrine)
    act_type, prefix = act_slugs(tipo)
    number = m.group("num").lower()
    year = int(m.group("year"))
    return ExtractedRef(
        act_ref=f"{prefix}-{number}-{year}",
        act_type=act_type,
        number=number,
        year=year,
        article=article,
        comma=comma,
    )


# ---------------------------------------------------------------------------
# Prose: date estremi + internal comma
# ---------------------------------------------------------------------------

# "<tipo> [del] <D>[°] <month> <YYYY>[,] n. <N>" — the E1-documented miss.
# Unlike the E1 slash form, the "n." token is REQUIRED: prose estremi always
# carry it, and without it any "legge <date>" narrative would fire. The
# match is rewritten to "<tipo> <N>/<YYYY>" and handed to the E1 grammar,
# which owns article binding, adjacency and overlap resolution.
_DATE_ESTREMI_RE = re.compile(
    rf"(?<![{_L}])"
    rf"(?P<tipo>{_ESTREMI_TIPO})"
    rf"\s+(?:del\s+)?\d{{1,2}}°?\s+(?:{'|'.join(_MONTHS)})\s+(?P<year>1[89]\d\d|20\d\d)"
    rf"\s*,?\s*(?:n\.?|n°|num\.?|numero)\s*"
    rf"(?P<num>\d{{1,4}}(?:[\s\-](?:{SUFFIX_ALT})(?![{_L}]))?)(?!\d)",
    re.IGNORECASE,
)

# Internal rinvio to the citing article's own comma. The negative lookahead
# keeps "di cui al comma 1 dell'articolo 6" out: that comma belongs to
# article 6 (which the E1 grammar emits on its own).
_INTERNAL_COMMA_RE = re.compile(
    rf"di\s+cui\s+al\s+comma\s+"
    rf"(?P<comma>\d+(?:[\s.\-]+(?:{SUFFIX_ALT})(?![{_L}]))?)"
    rf"(?!\s*,?\s*dell['’]\s*art)",
    re.IGNORECASE,
)


def _normalize_date_estremi(text: str) -> str:
    return _DATE_ESTREMI_RE.sub(
        lambda m: f"{m.group('tipo')} {_norm_number(m.group('num'))}/{m.group('year')}", text
    )


def extract_prose_refs(text: str) -> list[ExtractedRef]:
    """Extract the rinvii found inside norm text (pure, no I/O).

    URN-link refs first, then prose refs; deduplicated on (act_ref, article,
    comma). Internal rinvii come back unbound: ``act_ref=None`` for "di cui
    all'articolo N" (same act), additionally ``article=None`` for "di cui al
    comma N" (same article) — :func:`follow_citations` binds them against
    the citing hit.
    """
    if not text or not text.strip():
        return []
    urn_refs: list[ExtractedRef] = []

    def _consume_link(m: re.Match[str]) -> str:
        ref = _ref_from_urn(m.group(2))
        if ref is None:
            return m.group(1)  # keep the anchor prose, drop only the URL
        urn_refs.append(ref)
        return " "  # URN wins: the anchor must not be re-parsed

    prose = _MD_LINK_RE.sub(_consume_link, text)
    refs = urn_refs + extract_refs(_normalize_date_estremi(prose))
    refs.extend(
        ExtractedRef(comma=_norm_number(m.group("comma")))
        for m in _INTERNAL_COMMA_RE.finditer(prose)
    )
    seen: set[tuple[str | None, str | None, str | None]] = set()
    out: list[ExtractedRef] = []
    for ref in refs:
        key = (ref.act_ref, ref.article, ref.comma)
        if key not in seen:
            seen.add(key)
            out.append(ref)
    return out


# ---------------------------------------------------------------------------
# Following: hits -> new hits (one hop, budgeted)
# ---------------------------------------------------------------------------


def _bind_internal(ref: ExtractedRef, hit: SearchHit) -> ExtractedRef:
    """Bind an unbound rinvio to the hit it was extracted from."""
    if ref.act_ref is not None:
        return ref
    if ref.article is not None:
        return ref.model_copy(update={"act_ref": hit.act_ref})
    # comma-only internal ref: the citing article itself
    return ref.model_copy(update={"act_ref": hit.act_ref, "article": hit.article})


def follow_citations(
    hits: list[SearchHit],
    *,
    qdrant_client: QdrantClient,
    collection: str,
    engine: Engine | None = None,
    token_budget: int = 4000,
) -> list[SearchHit]:
    """Resolve the rinvii cited by ``hits``; returns ONLY the new hits.

    Refs are extracted from every hit's text (internal ones bound to the
    citing hit's own act/article), deduplicated against the acts/articles
    already present in ``hits`` — an article ref is dropped when that exact
    (act_ref, article) is already there, an act-level ref when ANY hit from
    that act is (this also silences each chunk's own header line, which
    names its act) — then resolved one (act_ref, article) target at a time
    via :func:`~legger.retrieval.fastpath.resolve_refs`, most-cited target
    first (count of citing hit texts; ties in first-seen order).

    ``token_budget`` caps the APPENDED context (estimate: len(text)//4 per
    chunk) and is a hard stop, not best-fit: the first chunk that does not
    fit ends the follow — a partially fetched article or a lower-frequency
    ref squeezed in instead would be worse context than none.

    One hop by design: the returned hits' own citations are NOT followed.
    The ``engine`` probe and the Qdrant error contract are resolve_refs's
    (Postgres degrades gracefully, Qdrant errors propagate).
    """
    if not hits or token_budget <= 0:
        return []
    have_articles = {(h.act_ref, h.article) for h in hits}
    have_acts = {h.act_ref for h in hits}
    seen_chunks = {h.chunk_id for h in hits}
    counts: dict[tuple[str, str | None], int] = {}
    by_target: dict[tuple[str, str | None], ExtractedRef] = {}
    for hit in hits:
        for ref in extract_prose_refs(hit.text):
            bound = _bind_internal(ref, hit)
            if bound.act_ref is None:
                continue
            target = (bound.act_ref, bound.article)
            counts[target] = counts.get(target, 0) + 1
            by_target.setdefault(target, bound)
    first_seen = {target: i for i, target in enumerate(by_target)}
    ordered = sorted(by_target, key=lambda t: (-counts[t], first_seen[t]))
    spent = 0
    new_hits: list[SearchHit] = []
    for act_ref, article in ordered:
        if (act_ref, article) in have_articles or (article is None and act_ref in have_acts):
            continue
        resolved = resolve_refs(
            [by_target[(act_ref, article)]],
            qdrant_client=qdrant_client,
            collection=collection,
            engine=engine,
        )
        for new_hit in resolved:
            if new_hit.chunk_id in seen_chunks:
                continue
            cost = max(1, len(new_hit.text) // 4)
            if spent + cost > token_budget:
                return new_hits
            seen_chunks.add(new_hit.chunk_id)
            new_hits.append(new_hit)
            spent += cost
    return new_hits
