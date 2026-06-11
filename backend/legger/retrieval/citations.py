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
     and they survive chunking, so they are machine-precise and PREFERRED —
     but only when the URN pins an ARTICLE (``~art``): then the ref comes
     from the URN and the anchor prose is consumed (never re-parsed —
     anchor and URN can disagree on details like comma lists, and the URN
     wins). ACT-ONLY URNs are NOT emitted from the URN: the corpus
     routinely spells the article in prose BEFORE such a link ("articolo
     96 del citato [decreto legislativo n. 117 del 2017](...:2017;117)"),
     so the link is replaced by its anchor text and the prose grammar
     binds article -> estremi by adjacency — emitting the act-level URN
     ref here would orphan the prose article onto the CITING act. Links
     whose URN does not parse at all (Costituzione URNs carry no numero,
     unknown codici) fall back the same way. Either way the URL itself
     never reaches the prose grammar; when the anchor of an act-only URN
     does not parse as estremi (rare), the ref degrades to
     act-level-or-nothing.
  2. **Prose.** Date estremi ("<tipo> <D month YYYY>, n. <N>" -> slug
     ``<tipo>-<N>-<YYYY>``, the E1-documented miss) are normalized to the
     "<tipo> <N>/<YYYY>" form, anaphoric qualifiers in the article->source
     gap ("del citato/medesimo/stesso decreto ...") are stripped, and the
     E1 grammar does the rest (article binding, codici names, adjacency);
     plus the internal "di cui al comma N" (act_ref=None AND article=None
     — the citing article's own comma).

  Ordering: URN refs first (in textual order), then prose refs (in textual
  order of the link-stripped text); deduplicated on (act_ref, article,
  comma) like :func:`~legger.retrieval.fastpath.extract_refs`.

  Documented misses (the citing chunk still reaches the LLM; only the
  follow degrades): article RANGES bind the first article only ("dagli
  articoli 282 a 284 del codice..." -> art. 282; "a"/"al" is not a list
  separator and range expansion is not attempted); adjacency gaps the E1
  grammar does not cross, e.g. "articolo N, primo periodo, del [link]"
  (ordinal "periodo" clauses) and "articolo N del codice di cui al
  [link]", degrade to an unbound article plus an act-level ref.

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
    _existing_acts,
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
# article 6 (which the E1 grammar emits on its own). Known noise: only the
# "dell'art..." continuation is blocked, so "di cui al comma 2 della legge
# n. ..." ALSO fires and emits a spurious internal ref next to the act ref.
# Harmless in follow_citations BY CONSTRUCTION — the internal ref binds to
# the citing article, which is always among the hits already and dedups
# away — but future consumers of extract_prose_refs should expect it.
_INTERNAL_COMMA_RE = re.compile(
    rf"di\s+cui\s+al\s+comma\s+"
    rf"(?P<comma>\d+(?:[\s.\-]+(?:{SUFFIX_ALT})(?![{_L}]))?)"
    rf"(?!\s*,?\s*dell['’]\s*art)",
    re.IGNORECASE,
)


# Anaphoric qualifiers between an article and its source ("articolo 96 del
# citato decreto legislativo n. 117 del 2017", "della medesima legge"): the
# E1 adjacency gap admits connectives only, so these words would break the
# article->estremi bind. Stripped before the grammar runs — norm-text style
# only (chat queries rarely use them), and the words are pure qualifiers,
# never sources or article numbers themselves.
_ANAPHORIC_RE = re.compile(
    rf"(?<![{_L}])(?:citat|medesim|stess|predett|suddett|richiamat|menzionat)[oaie]\s+",
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
        if ref is None or ref.article is None:
            # No parseable URN, or an ACT-ONLY URN: keep the anchor prose,
            # drop only the URL. Act-only links often have their article
            # spelled in prose BEFORE the anchor ("articolo 96 del citato
            # [decreto legislativo n. 117 del 2017](...)"): the prose
            # grammar binds it to the anchor's estremi by adjacency, while
            # consuming the link here would orphan that article onto the
            # CITING act (and lose the truly cited one).
            return m.group(1)
        urn_refs.append(ref)
        return " "  # URN wins: the anchor must not be re-parsed

    prose = _MD_LINK_RE.sub(_consume_link, text)
    refs = urn_refs + extract_refs(_ANAPHORIC_RE.sub("", _normalize_date_estremi(prose)))
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
    first. "Most-cited" counts DISTINCT extracted refs, not citing hits: a
    hit citing art. 14 comma 1 AND art. 14 comma 2 contributes two to the
    (act, art. 14) target. Ties resolve in first-seen order.

    ``token_budget`` caps the APPENDED context (estimate: len(text)//4 per
    chunk) and is a hard stop, not best-fit: the first chunk that does not
    fit ends the follow — a partially fetched article or a lower-frequency
    ref squeezed in instead would be worse context than none.

    One hop by design: the returned hits' own citations are NOT followed.
    The ``engine`` acts-table probe (estremi-COMPUTED refs only, same
    advisory semantics as resolve_refs's own: Postgres errors degrade to
    probe-less) is batched into ONE query over all targets up front;
    Qdrant errors propagate (resolve_refs's contract).
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
    ordered = [
        (act_ref, article)
        for act_ref, article in sorted(by_target, key=lambda t: (-counts[t], first_seen[t]))
        if (act_ref, article) not in have_articles
        and not (article is None and act_ref in have_acts)
    ]
    # One acts-table probe for ALL estremi-computed targets (resolve_refs
    # would otherwise re-probe per call below); resolve_refs runs probe-less.
    known_acts: set[str] | None = None
    if engine is not None:
        probe = sorted({t[0] for t in ordered if by_target[t].year is not None})
        if probe:
            known_acts = _existing_acts(engine, probe)
    spent = 0
    new_hits: list[SearchHit] = []
    for target in ordered:
        ref = by_target[target]
        if known_acts is not None and ref.year is not None and ref.act_ref not in known_acts:
            continue
        resolved = resolve_refs([ref], qdrant_client=qdrant_client, collection=collection)
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
