"""Tests for legger.retrieval.fastpath (Task E1).

``extract_refs`` is pure (no I/O): the grammar table below is the contract.
Precision >> recall — a false extraction sends garbage chunks to the LLM,
a missed one just falls back to hybrid search — so the NEGATIVE cases are
as load-bearing as the positives.

``resolve_refs`` is tested against a fake Qdrant client (scroll API) and an
in-memory SQLite ``acts`` table (the existence check is plain SQLAlchemy
Core, dialect-independent).
"""

from typing import Any

import pytest
from qdrant_client import models
from sqlalchemy import create_engine

from legger.db import acts
from legger.retrieval.fastpath import ExtractedRef, extract_refs, resolve_refs

# ---------------------------------------------------------------------------
# extract_refs: the grammar table
# ---------------------------------------------------------------------------


def triples(query: str) -> list[tuple[str | None, str | None, str | None]]:
    """(act_ref, article, comma) view of the extraction, for compact tables."""
    return [(r.act_ref, r.article, r.comma) for r in extract_refs(query)]


EXTRACTION_TABLE = [
    # --- codici abbreviations (dot/case-flexible) ---------------------------
    ("art. 2051 c.c.", [("codice-civile", "2051", None)]),
    ("Art. 2051 C.C.", [("codice-civile", "2051", None)]),
    ("art. 575 c.p.", [("codice-penale", "575", None)]),
    ("art. 700 c.p.c.", [("codice-procedura-civile", "700", None)]),
    # q04 benchmark miss: the c.p.p. is generational, its act_ref is the carrier.
    ("art. 274 c.p.p.", [("dpr-447-1988", "274", None)]),
    ("art. 186 cds", [("dlgs-285-1992", "186", None)]),
    ("art. 142 c.d.s.", [("dlgs-285-1992", "142", None)]),
    ("art. 2043 cod. civ.", [("codice-civile", "2043", None)]),
    ("art. 51 cod. pen.", [("codice-penale", "51", None)]),
    ("art. 360 cod. proc. civ.", [("codice-procedura-civile", "360", None)]),
    # --- codici by full name -------------------------------------------------
    ("articolo 52 del codice penale", [("codice-penale", "52", None)]),
    ("articolo 613-bis del codice penale", [("codice-penale", "613-bis", None)]),
    ("articolo 613 bis del codice penale", [("codice-penale", "613-bis", None)]),
    # q05 benchmark miss: no registry slug, the CdS act_ref is the carrier dlgs.
    ("articolo 186 del codice della strada", [("dlgs-285-1992", "186", None)]),
    # q08 benchmark miss: short common name for the d.lgs. 196/2003.
    ("articolo 167 del codice privacy", [("codice-privacy", "167", None)]),
    ("art. 122 del codice di procedura penale", [("dpr-447-1988", "122", None)]),
    ("art. 33 del codice del consumo", [("codice-consumo", "33", None)]),
    (
        "art. 20 del codice dell'amministrazione digitale",
        [("codice-amministrazione-digitale", "20", None)],
    ),
    (
        "art. 45 del codice della proprietà industriale",
        [("codice-proprieta-industriale", "45", None)],
    ),
    # bare codice name, no article: act-level ref (act_ref only)
    ("cosa prevede il codice del consumo?", [("codice-consumo", None, None)]),
    # --- estremi (tipo + numero + anno) --------------------------------------
    ("d.lgs. 81/2008 art 18", [("dlgs-81-2008", "18", None)]),
    ("D.Lgs. n. 81 del 2008", [("dlgs-81-2008", None, None)]),
    ("art. 18 del decreto legislativo 81/2008", [("dlgs-81-2008", "18", None)]),
    # q09 benchmark miss: estremi citation of the testo unico ambientale.
    ("art. 256 del d.lgs. 152/2006", [("dlgs-152-2006", "256", None)]),
    ("DL 77/2021", [("dl-77-2021", None, None)]),
    ("d.l. 77/2021", [("dl-77-2021", None, None)]),  # NOT dlgs!
    ("decreto-legge n. 18 del 2020", [("dl-18-2020", None, None)]),
    ("l. 197/2022 art. 1", [("legge-197-2022", "1", None)]),
    ("legge n. 197 del 2022", [("legge-197-2022", None, None)]),
    ("art. 28 del d.p.r. n. 600 del 1973", [("dpr-600-1973", "28", None)]),
    ("DPR 447/1988", [("dpr-447-1988", None, None)]),
    ("r.d. 262/1942", [("rd-262-1942", None, None)]),
    ("art. 1 del regio decreto 262/1942", [("rd-262-1942", "1", None)]),
    # --- commi ----------------------------------------------------------------
    ("art. 18, comma 1, del d.lgs. 81/2008", [("dlgs-81-2008", "18", "1")]),
    ("art. 18 co. 2 d.lgs. 81/2008", [("dlgs-81-2008", "18", "2")]),
    # "c. 1" is a comma (digit follows); "c.c." is a codice (letter follows).
    ("art. 18, c. 1, del d.lgs. 81/2008", [("dlgs-81-2008", "18", "1")]),
    ("art. 1341 c. 2 c.c.", [("codice-civile", "1341", "2")]),
    ("art. 186, comma 2-bis, del codice della strada", [("dlgs-285-1992", "186", "2-bis")]),
    ("art. 2051 c.c., comma 1", [("codice-civile", "2051", "1")]),
    # multi-commi list: the first comma is kept, the rest stays connective text
    ("art. 1, commi 2 e 3, c.c.", [("codice-civile", "1", "2")]),
    # ordinal comma, the classic civil-law citation style
    ("art. 1341, secondo comma, c.c.", [("codice-civile", "1341", "2")]),
    ("comma 2", []),  # a comma with no article and no act is nothing
    # --- multiple articles ----------------------------------------------------
    ("artt. 2043 e 2051 c.c.", [("codice-civile", "2043", None), ("codice-civile", "2051", None)]),
    (
        "artt. 414, 415 e 416 c.p.",
        [
            ("codice-penale", "414", None),
            ("codice-penale", "415", None),
            ("codice-penale", "416", None),
        ],
    ),
    (
        "articoli 1341 e 1342 del codice civile",
        [("codice-civile", "1341", None), ("codice-civile", "1342", None)],
    ),
    # --- article-only (no source nearby): act_ref None, caller may bind later --
    ("cosa prevede l'articolo 18?", [(None, "18", None)]),
    ("art. 2051", [(None, "2051", None)]),
    # adjacency: source too far from the article -> two INDEPENDENT refs
    (
        "l'art. 18 è importante; vedi anche il d.lgs. 152/2006",
        [(None, "18", None), ("dlgs-152-2006", None, None)],
    ),
    # two adjacent citations stay separate
    (
        "art. 14 del d.lgs. 81/2008 e art. 256 del d.lgs. 152/2006",
        [("dlgs-81-2008", "14", None), ("dlgs-152-2006", "256", None)],
    ),
    # two act-level citations in one sentence
    (
        "legge 104/1992 e d.lgs. 81/2008",
        [("legge-104-1992", None, None), ("dlgs-81-2008", None, None)],
    ),
    # unknown sources stay unknown: NOT misread as a codice abbreviation
    ("art. 36 del c.c.n.l. metalmeccanici", [(None, "36", None)]),
    ("art. 18 dello statuto dei lavoratori", [(None, "18", None)]),
    # --- Costituzione: recognized but NOT in the corpus -> dropped entirely ---
    # (an act_ref-None ref would let the caller bind art. 32 to the wrong act)
    ("art. 32 Cost.", []),
    ("articolo 117 della Costituzione", []),
    # --- negatives (the reason this is TDD-heavy) ------------------------------
    ("posso licenziare in malattia?", []),
    ("ho 81 anni e nel 2008 ho avuto un incidente", []),
    ("l'articolo di giornale parlava della legge", []),
    ("porto d'armi", []),
    ("negli anni 90/2000 la giurisprudenza era diversa", []),
    ("il decreto legislativo è una fonte del diritto", []),
    ("ci sono 81/2008 probabilità", []),
    ("", []),
    ("   ", []),
]


@pytest.mark.parametrize(
    "query,expected", EXTRACTION_TABLE, ids=[q or "<empty>" for q, _ in EXTRACTION_TABLE]
)
def test_extraction_table(query: str, expected: list[tuple]) -> None:
    assert triples(query) == expected


def test_estremi_fill_type_number_year() -> None:
    (ref,) = extract_refs("art. 18 del d.lgs. 81/2008")
    assert ref.act_ref == "dlgs-81-2008"
    assert ref.act_type == "decreto_legislativo"
    assert ref.number == "81"
    assert ref.year == 2008
    assert ref.article == "18"


def test_codice_refs_have_codice_act_type_and_no_estremi() -> None:
    (ref,) = extract_refs("art. 2051 c.c.")
    assert ref.act_type == "codice"
    assert ref.number is None
    assert ref.year is None


def test_duplicate_refs_are_deduped() -> None:
    assert triples("art. 2051 c.c. ... di nuovo art. 2051 c.c.") == [
        ("codice-civile", "2051", None)
    ]


# ---------------------------------------------------------------------------
# resolve_refs: fake Qdrant scroll + in-memory acts table
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


@pytest.fixture
def engine():
    eng = create_engine("sqlite://")
    acts.create(eng)
    with eng.begin() as conn:
        conn.execute(
            acts.insert(),
            [
                {
                    "act_ref": ref,
                    "act_type": "decreto_legislativo",
                    "collection": "Codici",
                    "vigenza": "vigente",
                    "file_path": f"Codici/{ref}.md",
                }
                for ref in ("dlgs-81-2008", "dlgs-152-2006")
            ],
        )
    return eng


def ref(act_ref: str | None = None, **kw: Any) -> ExtractedRef:
    return ExtractedRef(act_ref=act_ref, **kw)


def test_resolve_article_ref_filters_act_and_article(engine) -> None:
    client = FakeQdrant([payload("codice-civile", "2051"), payload("codice-civile", "2052")])
    hits = resolve_refs(
        [ref("codice-civile", act_type="codice", article="2051")],
        engine=engine,
        qdrant_client=client,
        collection="norme_test",
    )
    (hit,) = hits
    assert hit.act_ref == "codice-civile"
    assert hit.article == "2051"
    assert hit.score == 1.0  # synthetic score: explicit refs are exact matches
    assert hit.chunk_id == "codice-civile#art-2051#0"
    # the scroll filter carries both act_ref and article
    keys = {c.key for c in client.calls[0]["filter"].must}
    assert keys == {"act_ref", "article"}


def test_resolve_orders_article_splits_and_caps(engine) -> None:
    seqs = list(range(20))
    client = FakeQdrant([payload("codice-civile", "2051", seq=s) for s in reversed(seqs)])
    hits = resolve_refs(
        [ref("codice-civile", act_type="codice", article="2051")],
        engine=engine,
        qdrant_client=client,
        collection="norme_test",
    )
    assert len(hits) == 12  # article fetch capped
    assert [h.chunk_id for h in hits] == [f"codice-civile#art-2051#{s}" for s in seqs[:12]]


def test_resolve_act_level_ref_first_articles_only(engine) -> None:
    # act-level ref (no article): first ~5 chunks in ARTICLE order, not scroll order
    arts = ["10", "2-bis", "1", "3", "2", "1142", "annesso"]
    client = FakeQdrant([payload("codice-civile", a) for a in arts])
    hits = resolve_refs(
        [ref("codice-civile", act_type="codice")],
        engine=engine,
        qdrant_client=client,
        collection="norme_test",
    )
    assert [h.article for h in hits] == ["1", "2", "2-bis", "3", "10"]


def test_resolve_estremi_ref_checked_against_acts_table(engine) -> None:
    client = FakeQdrant([payload("dlgs-9999-2099", "1")])
    hits = resolve_refs(
        [
            ref(
                "dlgs-9999-2099",
                act_type="decreto_legislativo",
                number="9999",
                year=2099,
                article="1",
            )
        ],
        engine=engine,
        qdrant_client=client,
        collection="norme_test",
    )
    assert hits == []  # slug computed from estremi but unknown to Postgres -> miss
    assert client.calls == []  # no Qdrant roundtrip for a known-miss


def test_resolve_estremi_ref_present_in_acts_table(engine) -> None:
    client = FakeQdrant([payload("dlgs-152-2006", "256")])
    hits = resolve_refs(
        [
            ref(
                "dlgs-152-2006",
                act_type="decreto_legislativo",
                number="152",
                year=2006,
                article="256",
            )
        ],
        engine=engine,
        qdrant_client=client,
        collection="norme_test",
    )
    assert [h.chunk_id for h in hits] == ["dlgs-152-2006#art-256#0"]


def test_resolve_without_engine_goes_straight_to_qdrant() -> None:
    client = FakeQdrant([payload("dlgs-152-2006", "256")])
    hits = resolve_refs(
        [
            ref(
                "dlgs-152-2006",
                act_type="decreto_legislativo",
                number="152",
                year=2006,
                article="256",
            )
        ],
        engine=None,
        qdrant_client=client,
        collection="norme_test",
    )
    assert len(hits) == 1


def test_resolve_qdrant_miss_returns_empty(engine) -> None:
    client = FakeQdrant([])
    hits = resolve_refs(
        [ref("codice-civile", act_type="codice", article="9999")],
        engine=engine,
        qdrant_client=client,
        collection="norme_test",
    )
    assert hits == []  # caller falls back to hybrid


def test_resolve_skips_unbound_article_refs(engine) -> None:
    client = FakeQdrant([payload("codice-civile", "18")])
    hits = resolve_refs(
        [ref(None, article="18")],  # act_ref unknown: context binding is E5's job
        engine=engine,
        qdrant_client=client,
        collection="norme_test",
    )
    assert hits == []
    assert client.calls == []


def test_resolve_dedups_chunks_across_refs(engine) -> None:
    client = FakeQdrant([payload("codice-civile", "2051")])
    hits = resolve_refs(
        [
            ref("codice-civile", act_type="codice", article="2051"),
            ref("codice-civile", act_type="codice", article="2051", comma="1"),
        ],
        engine=engine,
        qdrant_client=client,
        collection="norme_test",
    )
    assert len(hits) == 1


def test_resolve_preserves_ref_order(engine) -> None:
    client = FakeQdrant([payload("codice-penale", "575"), payload("codice-civile", "2051")])
    hits = resolve_refs(
        [
            ref("codice-civile", act_type="codice", article="2051"),
            ref("codice-penale", act_type="codice", article="575"),
        ],
        engine=engine,
        qdrant_client=client,
        collection="norme_test",
    )
    assert [h.act_ref for h in hits] == ["codice-civile", "codice-penale"]
