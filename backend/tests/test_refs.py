"""Tests for canonical act_ref and vigenza derivation (Task B4).

Expected values for the fixture-driven cases were read by hand from the
fixtures' first h1 (see ``tests/fixtures/README.md``); the synthetic cases
cover the URN and filename fallback paths plus the never-crash contract.
"""

from pathlib import Path

import pytest

from legger.corpus.models import Act, Article, Comma
from legger.corpus.parser import parse_act
from legger.corpus.refs import (
    _KNOWN_CODICI,
    SOURCE_RANK,
    ActRef,
    act_slugs,
    act_type_from_collection,
    derive_act_ref,
    vigenza_from_path,
)

FIXTURES = Path(__file__).parent / "fixtures" / "corpus"

# Fixture rel_paths (POSIX, collection as first segment -- exactly as in the
# real corpus).
PROTEZIONE_CIVILE = "Codici/Codice della protezione civile. 18G00011.md"
DUPLICE_USO = (
    "Atti di attuazione Regolamenti UE/Attuazione del regolamento CE n. 3381-94 "
    "e della decisione n. 94-942-PESC sullesportazione di beni a duplice uso.md"
)
VINO = (
    "Atti di attuazione Regolamenti UE/Modalita di consegna del vino in distilleria "
    "inapplicazione dellart. 1 del regolamento CEE n. 1410-87 peri produttori che "
    "nella campagna 1986-87 non hannotrasformato i mosti in mosti concentrati.md"
)
CODICE_PENALE = "Codici/Approvazione del testo definitivo del Codice Penale. 030U1398.md"
CODICE_CIVILE = "Codici/1942-04-04_042U0262_VIGENZA_2026-04-29_V0.md"
REGIO_343 = "Regi decreti/012U0343.md"
BILANCIO = (
    "Leggi finanziarie e di bilancio/Bilancio di previsione dello Stato per lanno "
    "finanziario 2025 e bilancio pluriennale per il triennio 2025-2027. 24G00229.md"
)
ANNULLAMENTO = "Atti normativi abrogati (in originale)/Annullamento di partita 020U1371.md"
DECADUTO = "DL decaduti/Disposizioni urgenti in materia sanitaria_11.md"


def parse_fixture(rel_path: str) -> Act:
    return parse_act(FIXTURES / rel_path)


def make_act(title: str | None = None, body: str = "testo", subtitle: str | None = None) -> Act:
    return Act(
        title=title,
        subtitle=subtitle,
        articles=[Article(number="unico", commi=[Comma(number=None, text=body)])],
    )


# ---------------------------------------------------------------------------
# vigenza_from_path (A7)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rel_path", "expected"),
    [
        (ANNULLAMENTO, "abrogato"),
        (DECADUTO, "decaduto"),
        (PROTEZIONE_CIVILE, "vigente"),
        (REGIO_343, "vigente"),
        (BILANCIO, "vigente"),
        # Unknown collection -> vigente (A7: only the two folders flag state).
        ("Nuova collezione 2027/Atto qualunque.md", "vigente"),
        # The map is exact-name: near-misses do NOT flag the state.
        ("Atti normativi abrogati/Atto.md", "vigente"),
    ],
)
def test_vigenza_from_path(rel_path: str, expected: str) -> None:
    assert vigenza_from_path(rel_path) == expected


def test_vigenza_ignores_leading_slash() -> None:
    assert vigenza_from_path("/DL decaduti/Atto.md") == "decaduto"


# ---------------------------------------------------------------------------
# act_type_from_collection (complete map over the 23 real collections)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("collection", "expected"),
    [
        ("Atti di attuazione Regolamenti UE", "decreto"),
        ("Atti di recepimento direttive UE", "decreto_legislativo"),
        ("Atti normativi abrogati (in originale)", "atto_normativo"),
        ("Codici", "codice"),
        ("DL decaduti", "decreto_legge"),
        ("DL e leggi di conversione", "decreto_legge"),
        ("DL proroghe", "decreto_legge"),
        ("DPCM", "dpcm"),
        ("DPR", "dpr"),
        ("Decreti Legislativi", "decreto_legislativo"),
        ("Decreti legislativi luogotenenziali", "decreto_legislativo_luogotenenziale"),
        ("Leggi contenenti deleghe", "legge"),
        ("Leggi costituzionali", "legge_costituzionale"),
        ("Leggi delega e relativi provvedimenti delegati", "legge"),
        ("Leggi di delegazione europea", "legge"),
        ("Leggi di ratifica", "legge"),
        ("Leggi finanziarie e di bilancio", "legge"),
        ("Regi decreti", "regio_decreto"),
        ("Regi decreti legislativi", "regio_decreto_legislativo"),
        ("Regolamenti di delegificazione", "dpr"),
        ("Regolamenti governativi", "dpr"),
        ("Regolamenti ministeriali", "decreto_ministeriale"),
        ("Testi Unici", "testo_unico"),
    ],
)
def test_act_type_from_collection(collection: str, expected: str) -> None:
    assert act_type_from_collection(collection) == expected


def test_act_type_from_unknown_collection_is_slugified() -> None:
    assert act_type_from_collection("Ordinanze speciali (2027)") == "ordinanze_speciali_2027"


# ---------------------------------------------------------------------------
# act_slugs: TIPO string -> (act_type, ref prefix)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tipo", "expected"),
    [
        ("LEGGE", ("legge", "legge")),
        ("LEGGE COSTITUZIONALE", ("legge_costituzionale", "legge-cost")),
        ("DECRETO-LEGGE", ("decreto_legge", "dl")),
        ("DECRETO LEGGE", ("decreto_legge", "dl")),
        ("DECRETO LEGISLATIVO", ("decreto_legislativo", "dlgs")),
        (
            "DECRETO LEGISLATIVO LUOGOTENENZIALE",
            ("decreto_legislativo_luogotenenziale", "dlgs-lgt"),
        ),
        ("DECRETO LUOGOTENENZIALE", ("decreto_luogotenenziale", "dlt")),
        ("DECRETO-LEGGE LUOGOTENENZIALE", ("decreto_legge_luogotenenziale", "dl-lgt")),
        ("REGIO DECRETO", ("regio_decreto", "rd")),
        ("REGIO DECRETO-LEGGE", ("regio_decreto_legge", "rdl")),
        ("REGIO DECRETO LEGISLATIVO", ("regio_decreto_legislativo", "rdlgs")),
        ("D.P.R.", ("dpr", "dpr")),
        ("DECRETO DEL PRESIDENTE DELLA REPUBBLICA", ("dpr", "dpr")),
        ("DECRETO DEL PRESIDENTE DEL CONSIGLIO DEI MINISTRI", ("dpcm", "dpcm")),
        ("DECRETO MINISTERIALE", ("decreto_ministeriale", "dm")),
        ("DECRETO", ("decreto", "decreto")),
        # Unknown type: slugified, never a crash.
        ("ORDINANZA COMMISSARIALE", ("ordinanza_commissariale", "ordinanza-commissariale")),
    ],
)
def test_act_slugs(tipo: str, expected: tuple[str, str]) -> None:
    assert act_slugs(tipo) == expected


# ---------------------------------------------------------------------------
# derive_act_ref: header path (A3.a) over the real fixtures
# ---------------------------------------------------------------------------

FIXTURE_EXPECTATIONS = [
    # (rel_path, act_ref, act_type, number, year, date)
    (PROTEZIONE_CIVILE, "dlgs-1-2018", "decreto_legislativo", "1", 2018, "2018-01-02"),
    (DUPLICE_USO, "dlgs-89-1997", "decreto_legislativo", "89", 1997, "1997-02-24"),
    (VINO, "decreto-51-1988", "decreto", "51", 1988, "1988-01-20"),
    (CODICE_PENALE, "rd-1398-1930", "regio_decreto", "1398", 1930, "1930-10-19"),
    # AKN base64 file: the parser exposes the h1 from the decoded HTML, so the
    # Codice Civile still derives from the header, not from the filename.
    (CODICE_CIVILE, "rd-262-1942", "regio_decreto", "262", 1942, "1942-03-16"),
    (REGIO_343, "rd-343-1912", "regio_decreto", "343", 1912, "1912-04-11"),
    (BILANCIO, "legge-207-2024", "legge", "207", 2024, "2024-12-30"),
    (ANNULLAMENTO, "rd-1371-1920", "regio_decreto", "1371", 1920, "1920-06-24"),
    (DECADUTO, "dl-46-2000", "decreto_legge", "46", 2000, "2000-03-08"),
]


@pytest.mark.parametrize(
    ("rel_path", "act_ref", "act_type", "number", "year", "date"),
    FIXTURE_EXPECTATIONS,
    ids=[exp[1] for exp in FIXTURE_EXPECTATIONS],
)
def test_derive_act_ref_from_fixtures(
    rel_path: str, act_ref: str, act_type: str, number: str, year: int, date: str
) -> None:
    ref = derive_act_ref(parse_fixture(rel_path), rel_path)
    assert ref == ActRef(
        act_ref=act_ref,
        act_type=act_type,
        number=number,
        year=year,
        date=date,
        source="header",
    )


def test_act_refs_are_url_safe_and_stable() -> None:
    for rel_path, *_ in FIXTURE_EXPECTATIONS:
        act = parse_fixture(rel_path)
        first = derive_act_ref(act, rel_path)
        second = derive_act_ref(act, rel_path)
        assert first == second  # idempotence
        assert first.act_ref
        assert all(c.isascii() and (c.isalnum() or c == "-") for c in first.act_ref)
        assert first.act_ref == first.act_ref.lower()


def test_same_act_in_two_collections_same_act_ref() -> None:
    """Cross-collection dedup (A7): identical content -> identical act_ref."""
    act = parse_fixture(CODICE_PENALE)
    in_codici = derive_act_ref(act, CODICE_PENALE)
    in_abrogati = derive_act_ref(
        act, "Atti normativi abrogati (in originale)/Un altro nome di file.md"
    )
    assert in_codici.act_ref == in_abrogati.act_ref == "rd-1398-1930"


def test_header_with_comma_before_number() -> None:
    ref = derive_act_ref(make_act(title="LEGGE 30 dicembre 2022, n. 197"), "Codici/x.md")
    assert ref.act_ref == "legge-197-2022"
    assert ref.source == "header"
    assert ref.date == "2022-12-30"


def test_header_with_letter_suffix_number() -> None:
    ref = derive_act_ref(
        make_act(title="DECRETO LEGISLATIVO 15 novembre 1943 n. 7-B"),
        "Decreti Legislativi/x.md",
    )
    assert ref.act_ref == "dlgs-7-b-1943"
    assert ref.number == "7-b"


def test_header_lowercase_letter_suffix_is_not_taken() -> None:
    """Single-letter suffixes are uppercase-only BY DESIGN (see _HEADER).

    A lowercase letter would swallow title continuations ("n. 241 e
    successive modificazioni" -> "241-e", a wrong merge); the corpus scan
    found zero lowercase single-letter suffixes, so nothing is lost.
    """
    ref = derive_act_ref(
        make_act(title="DECRETO LEGISLATIVO 15 novembre 1943 n. 7-b"),
        "Decreti Legislativi/x.md",
    )
    assert ref.act_ref == "dlgs-7-1943"
    assert ref.number == "7"


def test_header_conjunction_after_number_is_not_a_suffix() -> None:
    ref = derive_act_ref(
        make_act(title="LEGGE 7 agosto 1990 n. 241 e successive modificazioni"),
        "Leggi contenenti deleghe/x.md",
    )
    assert ref.act_ref == "legge-241-1990"
    assert ref.number == "241"


@pytest.mark.parametrize(
    ("title", "act_ref", "number", "date"),
    [
        # 19th-century regi decreti number the act in roman numerals; the GU
        # codice redazionale confirms the arabic value (e.g. 9003875R -> 3875,
        # 0900343R -> 343). Note the additive non-subtractive forms (CCCC,
        # DCCCC) used by the Gazzetta.
        ("REGIO DECRETO 27 luglio 1890 n. MMMDCCCLXXV", "rd-3875-1890", "3875", "1890-07-27"),
        ("REGIO DECRETO 05 settembre 1909 n. CCCXLIII", "rd-343-1909", "343", "1909-09-05"),
        ("REGIO DECRETO 12 dicembre 1892 n. DCCCCXI", "rd-911-1892", "911", "1892-12-12"),
        ("REGIO DECRETO 05 ottobre 1884 n. MCCCCXLVIII", "rd-1448-1884", "1448", "1884-10-05"),
    ],
)
def test_header_with_roman_numeral_number(title: str, act_ref: str, number: str, date: str) -> None:
    ref = derive_act_ref(make_act(title=title), "Regi decreti/x.md")
    assert ref.source == "header"
    assert ref.act_ref == act_ref
    assert ref.number == number
    assert ref.date == date


def test_known_codice_title_without_number() -> None:
    """A bare codice h1 maps to the well-known registry slug."""
    ref = derive_act_ref(make_act(title="CODICE CIVILE"), "Codici/x.md")
    assert ref == ActRef(
        act_ref="codice-civile",
        act_type="codice",
        number=None,
        year=None,
        date=None,
        source="header",
    )


@pytest.mark.parametrize(
    ("title", "slug"),
    [
        ("Codice penale militare di pace", "codice-penale-militare-pace"),
        ("Codice penale militare di guerra", "codice-penale-militare-guerra"),
        # The article keeps working in front of the extended names too.
        ("Il Codice penale militare di pace", "codice-penale-militare-pace"),
    ],
)
def test_military_penal_codes_do_not_merge_into_codice_penale(title: str, slug: str) -> None:
    """The 'codice penale' entry must NOT swallow the military penal codes."""
    ref = derive_act_ref(make_act(title=title), "Codici/x.md")
    assert ref.act_ref == slug
    assert ref.act_type == "codice"
    assert ref.source == "header"


def test_known_codici_registry_is_sorted_longest_first() -> None:
    """Ordering invariant: longest-prefix-first, enforced at definition time.

    With non-increasing key lengths an extended name ("codice penale militare
    di pace") can never be shadowed by its prefix ("codice penale").
    """
    lengths = [len(name) for name, _ in _KNOWN_CODICI]
    assert lengths == sorted(lengths, reverse=True)


def test_attuazione_title_does_not_match_codice() -> None:
    """Documented invariant: the keyword must OPEN the title."""
    ref = derive_act_ref(
        make_act(title="Disposizioni per l'attuazione del Codice di procedura civile"),
        "Codici/x.md",
    )
    assert ref.act_ref != "codice-procedura-civile"
    assert ref.source == "filename"


def test_known_codice_strips_leading_article() -> None:
    """Documented invariant: removeprefix('il ') before the registry match."""
    ref = derive_act_ref(make_act(title="Il Codice Civile"), "Codici/x.md")
    assert ref.act_ref == "codice-civile"
    assert ref.source == "header"


def test_known_codice_requires_word_boundary() -> None:
    """The keyword matches whole words only: 'codice penale' must not match a
    mid-word extension of the keyword (synthetic title)."""
    ref = derive_act_ref(make_act(title="CODICE PENALESCO"), "Codici/x.md")
    assert ref.act_ref != "codice-penale"
    assert ref.source == "filename"


# ---------------------------------------------------------------------------
# derive_act_ref: URN fallback (A3.b)
# ---------------------------------------------------------------------------

URN_BODY = (
    "Vedi il [decreto](http://www.normattiva.it/uri-res/N2Ls?"
    "urn:nir:stato:decreto.legislativo:2018-01-02;1) in Gazzetta."
)


def test_urn_fallback_when_title_missing() -> None:
    ref = derive_act_ref(make_act(title=None, body=URN_BODY), "Codici/x.md")
    assert ref == ActRef(
        act_ref="dlgs-1-2018",
        act_type="decreto_legislativo",
        number="1",
        year=2018,
        date="2018-01-02",
        source="urn",
    )


def test_urn_fallback_when_title_unusable() -> None:
    ref = derive_act_ref(
        make_act(title="GAZZETTA UFFICIALE DELLA REPUBBLICA ITALIANA", body=URN_BODY),
        "Codici/x.md",
    )
    assert ref.act_ref == "dlgs-1-2018"
    assert ref.source == "urn"


def test_urn_codice_tipo_maps_to_registry_slug() -> None:
    body = (
        "Il [Codice Penale](http://www.normattiva.it/uri-res/N2Ls?"
        "urn:nir:stato:codice.penale:1930-10-19;1398) e' aggiornato."
    )
    ref = derive_act_ref(make_act(title=None, body=body), "Codici/x.md")
    assert ref.act_ref == "codice-penale"
    assert ref.act_type == "codice"
    assert ref.number == "1398"
    assert ref.year == 1930
    assert ref.source == "urn"


def test_urn_with_year_only_date() -> None:
    body = "[legge 241/1990](http://www.normattiva.it/uri-res/N2Ls?urn:nir:stato:legge:1990;241)"
    ref = derive_act_ref(make_act(title=None, body=body), "DPR/x.md")
    assert ref.act_ref == "legge-241-1990"
    assert ref.year == 1990
    assert ref.date is None
    assert ref.source == "urn"


def test_urn_military_codice_tipo_maps_to_military_slug() -> None:
    """URN tipo codice.penale.militare.* must not merge into codice-penale."""
    body = (
        "Vedi (http://www.normattiva.it/uri-res/N2Ls?"
        "urn:nir:stato:codice.penale.militare.di.pace:1941-02-20;303)."
    )
    ref = derive_act_ref(make_act(title=None, body=body), "Codici/x.md")
    assert ref.act_ref == "codice-penale-militare-pace"
    assert ref.act_type == "codice"
    assert ref.source == "urn"


def test_urn_in_subtitle_is_found() -> None:
    ref = derive_act_ref(
        make_act(title=None, subtitle=URN_BODY, body="testo senza link"),
        "Codici/x.md",
    )
    assert ref.act_ref == "dlgs-1-2018"
    assert ref.source == "urn"


def test_urn_in_second_comma_is_not_taken() -> None:
    """The URN scan stops after the FIRST comma: a foreign act cited from
    comma 2 onwards must never become the act's own identity."""
    foreign = (
        "Ai sensi della [legge 241/1990](http://www.normattiva.it/uri-res/N2Ls?"
        "urn:nir:stato:legge:1990;241) si applica."
    )
    act = Act(
        title=None,
        subtitle=None,
        articles=[
            Article(
                number="1",
                commi=[
                    Comma(number="1", text="testo senza alcun link"),
                    Comma(number="2", text=foreign),
                ],
            )
        ],
    )
    ref = derive_act_ref(act, DECADUTO)
    assert ref.act_ref != "legge-241-1990"
    assert ref.source == "filename"


# ---------------------------------------------------------------------------
# derive_act_ref: filename fallback (A3.c)
# ---------------------------------------------------------------------------


def test_filename_fallback_data_codice_vigenza() -> None:
    """The data_codice filename class yields the GU codice redazionale + date."""
    ref = derive_act_ref(make_act(title=None), CODICE_CIVILE)
    assert ref == ActRef(
        act_ref="gu-042u0262",
        act_type="codice",
        number=None,
        year=1942,
        date="1942-04-04",
        source="filename",
    )


def test_filename_fallback_codice_redazionale_only() -> None:
    ref = derive_act_ref(make_act(title=None), REGIO_343)
    assert ref.act_ref == "gu-012u0343"
    assert ref.act_type == "regio_decreto"
    assert ref.source == "filename"
    assert ref.date is None


def test_filename_fallback_trailing_codice_redazionale() -> None:
    ref = derive_act_ref(make_act(title=None), ANNULLAMENTO)
    assert ref.act_ref == "gu-020u1371"
    assert ref.source == "filename"


def test_filename_fallback_generic_slug() -> None:
    ref = derive_act_ref(make_act(title=None), DECADUTO)
    assert ref.act_ref == "dl-disposizioni-urgenti-in-materia-sanitaria-11"
    assert ref.act_type == "decreto_legge"
    assert ref.source == "filename"


def test_filename_truncation_disambiguates_with_stem_hash() -> None:
    """Two >100-char slugs sharing their first 100 chars must NOT collide:
    the truncated slug carries a stable hash of the full original stem."""
    shared = "Attuazione della direttiva " + "comunitaria " * 8  # > 100 slug chars
    path_a = f"Atti di recepimento direttive UE/{shared}sui rifiuti.md"
    path_b = f"Atti di recepimento direttive UE/{shared}sulle acque.md"
    ref_a = derive_act_ref(make_act(title=None), path_a)
    ref_b = derive_act_ref(make_act(title=None), path_b)
    assert ref_a.act_ref != ref_b.act_ref
    assert ref_a.source == ref_b.source == "filename"
    # Stable: same (content, path) -> same act_ref, with an 8-hex-char tail.
    assert ref_a == derive_act_ref(make_act(title=None), path_a)
    for ref in (ref_a, ref_b):
        tail = ref.act_ref.rsplit("-", 1)[1]
        assert len(tail) == 8
        assert all(c in "0123456789abcdef" for c in tail)
        assert all(c.isascii() and (c.isalnum() or c == "-") for c in ref.act_ref)


def test_filename_short_slug_is_not_hashed() -> None:
    """The hash tail only appears when truncation actually occurs."""
    ref = derive_act_ref(make_act(title=None), DECADUTO)
    assert ref.act_ref == "dl-disposizioni-urgenti-in-materia-sanitaria-11"


# ---------------------------------------------------------------------------
# Source ranking for D2 dedup
# ---------------------------------------------------------------------------


def test_source_rank_orders_header_urn_filename() -> None:
    assert SOURCE_RANK == {"header": 0, "urn": 1, "filename": 2}
    header = derive_act_ref(parse_fixture(PROTEZIONE_CIVILE), PROTEZIONE_CIVILE)
    urn = derive_act_ref(make_act(title=None, body=URN_BODY), "Codici/x.md")
    filename = derive_act_ref(make_act(title=None), DECADUTO)
    assert (header.rank, urn.rank, filename.rank) == (0, 1, 2)
    assert header.rank < urn.rank < filename.rank


def test_never_crashes_on_garbage() -> None:
    ref = derive_act_ref(make_act(title="???", body="!!!"), "Cartella ignota/###.md")
    assert ref.source == "filename"
    assert ref.act_ref
    assert all(c.isascii() and (c.isalnum() or c == "-") for c in ref.act_ref)
