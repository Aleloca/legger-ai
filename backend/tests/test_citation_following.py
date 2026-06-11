"""Tests for legger.retrieval.citations (Task E4): 1-hop citation following.

``extract_prose_refs`` is pure (no I/O) and extends the E1 grammar to the
rinvio forms found INSIDE norm text — the fixtures below are real corpus
snippets (Codice della protezione civile 18G00011, Codice Penale 030U1398).
Two extraction channels, URN first:

- URN NIR links already present in the corpus markdown are machine-precise
  and take PRECEDENCE: when a ``[anchor](...urn:nir:stato:...)`` link parses,
  the ref comes from the URN and the anchor prose is NOT re-parsed.
- Prose forms: date estremi ("decreto legislativo 9 aprile 2008, n. 81"),
  the E1 grammar (codici, "art. X del ..."), and the internal "di cui al
  comma 2" (act_ref=None, article=None — the citing article's own comma).

``follow_citations`` is tested against the same FakeQdrant scroll stand-in
used by test_fastpath: extraction from hit texts, internal binding to the
hit's own act, dedup against acts/articles already in the hits, citation
frequency ordering, hard token budget, and the 1-hop cap.
"""

from typing import Any

import pytest
from qdrant_client import models

from legger.retrieval.citations import extract_prose_refs, follow_citations
from legger.retrieval.search import SearchHit

# ---------------------------------------------------------------------------
# extract_prose_refs: the grammar table
# ---------------------------------------------------------------------------


def triples(text: str) -> list[tuple[str | None, str | None, str | None]]:
    """(act_ref, article, comma) view of the extraction, for compact tables."""
    return [(r.act_ref, r.article, r.comma) for r in extract_prose_refs(text)]


NORMATTIVA = "http://www.normattiva.it/uri-res/N2Ls?"

EXTRACTION_TABLE = [
    # --- prose date estremi (deferred from E1) ------------------------------
    (
        "le misure di cui all'articolo 14 del decreto legislativo 9 aprile 2008, n. 81",
        [("dlgs-81-2008", "14", None)],
    ),
    (
        "in conformita' di quanto previsto dal decreto legislativo 18 agosto 2000, n. 267, "
        "i Sindaci metropolitani e i Presidenti delle Regioni",
        [("dlgs-267-2000", None, None)],
    ),
    (
        "ai sensi dell'articolo 6 della legge 7 agosto 1990, n. 241",
        [("legge-241-1990", "6", None)],
    ),
    (
        "in conformita' a quanto previsto dalla legge 21 novembre 2000, n. 353, "
        "e successive modificazioni",
        [("legge-353-2000", None, None)],
    ),
    # abbreviated tipo with a prose date
    ("come previsto dal d.lgs. 9 aprile 2008, n. 81", [("dlgs-81-2008", None, None)]),
    # "1° gennaio" style day ordinal
    ("ai sensi della legge 1° agosto 2002, n. 166", [("legge-166-2002", None, None)]),
    # DPR spelled out with a prose date
    (
        "approvato con decreto del Presidente della Repubblica 28 dicembre 1985, n. 1092",
        [("dpr-1092-1985", None, None)],
    ),
    # decreto-legge with conversion law: two act-level refs
    (
        "il decreto-legge 31 maggio 2010, n. 78, convertito, con modificazioni, "
        "dalla legge 30 luglio 2010, n. 122",
        [("dl-78-2010", None, None), ("legge-122-2010", None, None)],
    ),
    # --- E1 grammar still applies inside norm text --------------------------
    ("ai sensi dell'art. 1341 del codice civile", [("codice-civile", "1341", None)]),
    ("ai sensi dell'articolo 135", [(None, "135", None)]),
    # --- internal refs (same act) --------------------------------------------
    ("le strutture operative e i soggetti concorrenti di cui al comma 2", [(None, None, "2")]),
    (
        "secondo quanto stabilito dalla pianificazione di cui all'articolo 18",
        [(None, "18", None)],
    ),
    # "di cui al comma 1 dell'articolo 6" is article 6's comma, NOT an
    # internal comma of the citing article: only the article ref is emitted.
    ("le funzioni di cui al comma 1 dell'articolo 6", [(None, "6", None)]),
    # --- URN NIR links (machine-precise, win over the anchor prose) ----------
    (
        "in conformita' di quanto previsto dal [decreto legislativo 18 agosto 2000, n. 267]"
        f"({NORMATTIVA}urn:nir:stato:decreto.legislativo:2000-08-18;267), i Sindaci",
        [("dlgs-267-2000", None, None)],
    ),
    (
        "ai sensi dell'[articolo 2, comma 2, del decreto legislativo 8 aprile 2003, n. 66]"
        f"({NORMATTIVA}urn:nir:stato:decreto.legislativo:2003-04-08;66~art2-com2) "
        "e successive modificazioni",
        [("dlgs-66-2003", "2", "2")],
    ),
    # anchor says "commi 27 e seguenti" (unparseable list), the URN pins com27
    (
        "anche in deroga alle previsioni di cui all'[articolo 14, commi 27 e seguenti, "
        f"del decreto-legge 31 maggio 2010, n. 78]({NORMATTIVA}"
        "urn:nir:stato:decreto.legge:2010-05-31;78~art14-com27)",
        [("dl-78-2010", "14", "27")],
    ),
    # year-only URN date, latin-suffix article (legge 225/1992 art. 1-bis)
    (
        f"([Articolo 1-bis, comma 1, legge 225/1992]({NORMATTIVA}"
        "urn:nir:stato:legge:1992;225~art1bis-com1))",
        [("legge-225-1992", "1-bis", "1")],
    ),
    # codice URN: the registry maps the name, not the carrier estremi
    (
        f"reati politici, ai sensi dell'[art. 8 del Codice penale]({NORMATTIVA}"
        "urn:nir:stato:codice.penale:1930-10-19;1398~art8), e i reati connessi",
        [("codice-penale", "8", None)],
    ),
    # Costituzione: URN has no numero (unparseable), anchor prose names Cost.
    # -> dropped entirely, per the E1 doctrine (not in the corpus).
    (
        "Visto l'[articolo 117, terzo comma, della Costituzione]"
        f"({NORMATTIVA}urn:nir:stato:costituzione:1947-12-27~art117-com3);",
        [],
    ),
    # unknown codice URN: not emitted, the anchor prose is the fallback
    (
        f"di cui all'[articolo 93 del codice di cui sopra]({NORMATTIVA}"
        "urn:nir:stato:codice.ignoto.mai.visto:2099;999~art93)",
        [(None, "93", None)],
    ),
    # --- negatives ------------------------------------------------------------
    ("", []),
    ("   \n  ", []),
    ("Il conducente di un veicolo e' obbligato a risarcire il danno.", []),
    # date without a numero is not an estremi
    ("la seduta del 9 aprile 2008 e' rinviata", []),
]


@pytest.mark.parametrize(("text", "expected"), EXTRACTION_TABLE)
def test_extraction_table(text: str, expected: list[tuple]) -> None:
    assert triples(text) == expected


def test_prose_date_estremi_fill_verification_fields() -> None:
    """number/year flag the act_ref as COMPUTED (E1 contract: probe before fetch)."""
    (ref,) = extract_prose_refs(
        "di cui all'articolo 14 del decreto legislativo 9 aprile 2008, n. 81"
    )
    assert ref.act_type == "decreto_legislativo"
    assert ref.number == "81"
    assert ref.year == 2008


def test_urn_estremi_fill_verification_fields() -> None:
    (ref,) = extract_prose_refs(
        f"[legge 7 aprile 2014, n. 56]({NORMATTIVA}urn:nir:stato:legge:2014-04-07;56)"
    )
    assert ref.act_ref == "legge-56-2014"
    assert ref.number == "56"
    assert ref.year == 2014


def test_urn_codice_ref_is_registry_verified() -> None:
    """Codice URNs map through the E1 registry: no number/year, no probe needed."""
    (ref,) = extract_prose_refs(
        f"[art. 8 del Codice penale]({NORMATTIVA}urn:nir:stato:codice.penale:1930-10-19;1398~art8)"
    )
    assert ref.act_ref == "codice-penale"
    assert ref.number is None
    assert ref.year is None


def test_urn_duplicate_of_prose_is_one_ref() -> None:
    """The same act cited once as a link and once as bare prose dedups."""
    text = (
        f"dal [decreto legislativo 18 agosto 2000, n. 267]({NORMATTIVA}"
        "urn:nir:stato:decreto.legislativo:2000-08-18;267) e, in coerenza, "
        "dal decreto legislativo 18 agosto 2000, n. 267"
    )
    assert triples(text) == [("dlgs-267-2000", None, None)]


# ---------------------------------------------------------------------------
# follow_citations: fakes (same shape as test_fastpath)
# ---------------------------------------------------------------------------


def payload(act_ref: str, article: str, seq: int = 0, **extra: Any) -> dict[str, Any]:
    return {
        "chunk_id": f"{act_ref}#art-{article}#{seq}",
        "act_ref": act_ref,
        "act_type": "codice",
        "act_title": "Titolo",
        "article": article,
        "commi": [],
        "collection": "Codici",
        "vigenza": "vigente",
        "file_path": "Codici/file.md",
        "header": f"Art. {article}",
        "text": f"testo art. {article} ({seq})",
        **extra,
    }


class FakeQdrant:
    """Minimal scroll() stand-in filtering an in-memory payload list."""

    def __init__(self, payloads: list[dict[str, Any]]) -> None:
        self.payloads = payloads
        self.calls: list[dict[str, Any]] = []

    def scroll(
        self,
        collection_name: str,
        *,
        scroll_filter: models.Filter | None = None,
        limit: int = 10,
        offset: Any = None,
        with_payload: bool = True,
        with_vectors: bool = False,
    ) -> tuple[list[models.Record], Any]:
        self.calls.append(
            {"collection_name": collection_name, "filter": scroll_filter, "limit": limit}
        )
        wanted = {c.key: c.match.value for c in (scroll_filter.must or [])} if scroll_filter else {}
        selected = [p for p in self.payloads if all(p.get(k) == v for k, v in wanted.items())]
        start = offset or 0
        page = selected[start : start + limit]
        records = [
            models.Record(id=f"00000000-0000-0000-0000-{i:012d}", payload=p)
            for i, p in enumerate(page, start=start)
        ]
        next_offset = start + limit if start + limit < len(selected) else None
        return records, next_offset


def hit(act_ref: str, article: str, text: str, seq: int = 0) -> SearchHit:
    """A retrieval hit as the E5 pipeline would hand it to follow_citations."""
    p = payload(act_ref, article, seq, text=text)
    return SearchHit(
        score=0.9,
        chunk_id=p["chunk_id"],
        act_ref=act_ref,
        article=article,
        act_title=p["act_title"],
        header=p["header"],
        text=text,
        vigenza="vigente",
        payload=p,
    )


def follow(hits: list[SearchHit], client: FakeQdrant, **kw: Any) -> list[SearchHit]:
    return follow_citations(hits, qdrant_client=client, collection="norme_test", **kw)


# ---------------------------------------------------------------------------
# follow_citations: behavior
# ---------------------------------------------------------------------------


def test_follows_prose_and_internal_refs() -> None:
    client = FakeQdrant(
        [
            payload("codice-protezione-civile", "18"),
            payload("legge-241-1990", "6"),
        ]
    )
    hits = [
        hit(
            "codice-protezione-civile",
            "6",
            "secondo quanto stabilito dalla pianificazione di cui all'articolo 18, "
            "ai sensi dell'articolo 6 della legge 7 agosto 1990, n. 241",
        )
    ]
    new = follow(hits, client)
    got = {(h.act_ref, h.article) for h in new}
    # the internal "articolo 18" bound to the hit's own act; the estremi resolved
    assert got == {("codice-protezione-civile", "18"), ("legge-241-1990", "6")}
    assert all(h.score == 1.0 for h in new)  # resolve_refs synthetic score


def test_returns_only_new_hits_dedup_on_articles_and_acts() -> None:
    client = FakeQdrant(
        [
            payload("codice-protezione-civile", "18"),
            payload("legge-241-1990", "6"),
        ]
    )
    hits = [
        hit(
            "codice-protezione-civile",
            "6",
            "la pianificazione di cui all'articolo 18, ai sensi della legge 7 agosto 1990, n. 241",
        ),
        # the cited article is ALREADY among the hits
        hit("codice-protezione-civile", "18", "testo della pianificazione"),
        # a hit from the cited act: the act-level ref must not re-fetch it
        hit("legge-241-1990", "1", "principi generali dell'attivita' amministrativa"),
    ]
    assert follow(hits, client) == []


def test_internal_comma_ref_dedups_to_the_citing_article() -> None:
    """ "di cui al comma 2" is the citing article's own comma: never a new fetch."""
    client = FakeQdrant([payload("codice-protezione-civile", "13")])
    hits = [
        hit(
            "codice-protezione-civile",
            "13",
            "i soggetti concorrenti di cui al comma 2 o con altri soggetti pubblici",
        )
    ]
    assert follow(hits, client) == []
    assert client.calls == []  # dedup happens before Qdrant is touched


def test_own_header_act_name_is_not_followed() -> None:
    """The chunk's own header line names its act; it must not echo back."""
    client = FakeQdrant([payload("codice-penale", "52"), payload("codice-penale", "614")])
    hits = [
        hit(
            "codice-penale",
            "52",
            "Codice 1398/1930 — Approvazione del testo definitivo del Codice Penale.\n"
            "Codice Penale\nArt. 52 — Difesa legittima\n\nArt. 52.\n\n"
            "Nei casi previsti dall'articolo 614, primo e secondo comma, sussiste",
        )
    ]
    new = follow(hits, client)
    assert {(h.act_ref, h.article) for h in new} == {("codice-penale", "614")}


def test_most_cited_first_and_budget_is_a_hard_stop() -> None:
    text_a = "a" * 400  # ~100 estimated tokens
    text_b = "b" * 400
    client = FakeQdrant(
        [
            payload("codice-civile", "1341", text=text_a),
            payload("codice-civile", "2043", text=text_b),
        ]
    )
    hits = [
        hit("dlgs-285-1992", "1", "ai sensi dell'art. 2043 del codice civile"),
        hit(
            "dlgs-285-1992",
            "2",
            "ai sensi dell'art. 1341 del codice civile e dell'art. 2043 del codice civile",
        ),
        hit("dlgs-285-1992", "3", "di nuovo l'art. 2043 del codice civile"),
    ]
    # 2043 is cited by three hits, 1341 by one: 2043 first; the budget only
    # fits one chunk, so the follow stops there (hard stop, not best-fit).
    new = follow(hits, client, token_budget=150)
    assert [(h.act_ref, h.article) for h in new] == [("codice-civile", "2043")]
    # with room for both, frequency still orders the appended hits
    new = follow(hits, client, token_budget=4000)
    assert [(h.act_ref, h.article) for h in new] == [
        ("codice-civile", "2043"),
        ("codice-civile", "1341"),
    ]


def test_resolve_misses_are_ignored() -> None:
    client = FakeQdrant([])  # cited act not in the corpus
    hits = [hit("codice-civile", "2054", "di cui al decreto legislativo 9 aprile 2008, n. 81")]
    assert follow(hits, client) == []


def test_no_refs_returns_empty() -> None:
    client = FakeQdrant([payload("codice-civile", "2054")])
    hits = [hit("codice-civile", "2054", "Il conducente e' obbligato a risarcire il danno.")]
    assert follow(hits, client) == []
    assert follow([], client) == []


def test_one_hop_only_fetched_chunks_are_not_refollowed() -> None:
    client = FakeQdrant(
        [
            # the fetched chunk itself cites the codice civile...
            payload(
                "legge-241-1990",
                "6",
                text="il responsabile valuta, ai sensi dell'art. 1341 del codice civile",
            ),
            # ...which IS resolvable in this fake corpus
            payload("codice-civile", "1341"),
        ]
    )
    hits = [hit("codice-protezione-civile", "6", "della legge 7 agosto 1990, n. 241")]
    new = follow(hits, client)
    assert {(h.act_ref, h.article) for h in new} == {("legge-241-1990", "6")}


def test_budget_zero_or_negative_returns_empty_without_io() -> None:
    client = FakeQdrant([payload("legge-241-1990", "6")])
    hits = [hit("codice-civile", "2054", "della legge 7 agosto 1990, n. 241")]
    assert follow(hits, client, token_budget=0) == []
    assert client.calls == []
