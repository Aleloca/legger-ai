"""Tests for the act parser (Task B3), driven by the real-corpus fixtures.

Every expected value below was read by hand from the fixture files (see
``tests/fixtures/README.md`` for the documented phenomena and truncation
boundaries, and ``docs/corpus-analysis.md`` assunzioni A1-A10).
"""

from pathlib import Path

import pytest

from legger.corpus.akn import parse_akn_html
from legger.corpus.models import Act, Comma
from legger.corpus.parser import parse_act, parse_act_text

FIXTURES = Path(__file__).parent / "fixtures" / "corpus"

PROTEZIONE_CIVILE = FIXTURES / "Codici" / "Codice della protezione civile. 18G00011.md"
DUPLICE_USO = (
    FIXTURES
    / "Atti di attuazione Regolamenti UE"
    / "Attuazione del regolamento CE n. 3381-94 e della decisione n. 94-942-PESC "
    "sullesportazione di beni a duplice uso.md"
)
VINO = (
    FIXTURES
    / "Atti di attuazione Regolamenti UE"
    / "Modalita di consegna del vino in distilleria inapplicazione dellart. 1 del regolamento "
    "CEE n. 1410-87 peri produttori che nella campagna 1986-87 non hannotrasformato i mosti "
    "in mosti concentrati.md"
)
CODICE_PENALE = (
    FIXTURES / "Codici" / "Approvazione del testo definitivo del Codice Penale. 030U1398.md"
)
CODICE_CIVILE = FIXTURES / "Codici" / "1942-04-04_042U0262_VIGENZA_2026-04-29_V0.md"
REGIO_343 = FIXTURES / "Regi decreti" / "012U0343.md"
BILANCIO = (
    FIXTURES
    / "Leggi finanziarie e di bilancio"
    / "Bilancio di previsione dello Stato per lanno finanziario 2025 e bilancio pluriennale "
    "per il triennio 2025-2027. 24G00229.md"
)
ANNULLAMENTO = (
    FIXTURES / "Atti normativi abrogati (in originale)" / "Annullamento di partita 020U1371.md"
)
DECADUTO = FIXTURES / "DL decaduti" / "Disposizioni urgenti in materia sanitaria_11.md"

ALL_FIXTURES = [
    PROTEZIONE_CIVILE,
    DUPLICE_USO,
    VINO,
    CODICE_PENALE,
    CODICE_CIVILE,
    REGIO_343,
    BILANCIO,
    ANNULLAMENTO,
    DECADUTO,
]


def article_text(act: Act, index: int) -> str:
    return "\n".join(c.text for c in act.articles[index].commi)


def comma_by_number(act: Act, art_index: int, number: str) -> Comma:
    for comma in act.articles[art_index].commi:
        if comma.number == number:
            return comma
    raise AssertionError(f"comma {number} not found in article index {art_index}")


# ---------------------------------------------------------------------------
# Smoke: every fixture parses, yields >= 1 article, and carries no NUL bytes.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ALL_FIXTURES, ids=lambda p: p.name)
def test_smoke_all_fixtures(path: Path) -> None:
    act = parse_act(path)
    assert isinstance(act, Act)
    assert len(act.articles) >= 1
    for article in act.articles:
        assert len(article.commi) >= 1
        assert "\x00" not in article.number
        for comma in article.commi:
            assert "\x00" not in comma.text
    if act.title is not None:
        assert "\x00" not in act.title


# ---------------------------------------------------------------------------
# A2/A3/A4.1/A5/A6/A8 - modern act with ATX articles (Codice protezione civile)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def protezione_civile() -> Act:
    return parse_act(PROTEZIONE_CIVILE)


class TestProtezioneCivile:
    def test_title_from_first_h1(self, protezione_civile: Act) -> None:
        assert protezione_civile.title == "DECRETO LEGISLATIVO 02 gennaio 2018 n. 1 (Raccolta 2018)"

    def test_subtitle_from_first_h2(self, protezione_civile: Act) -> None:
        assert protezione_civile.subtitle == "Codice della protezione civile. (18G00011)"

    def test_article_count_and_numbers(self, protezione_civile: Act) -> None:
        numbers = [a.number for a in protezione_civile.articles]
        assert len(numbers) == 51
        assert numbers[0] == "1"
        assert numbers[-1] == "50"
        assert "46-bis" in numbers  # ATX marker "### Art. 46-bis" (hyphen suffix form)

    def test_article_1_heading_and_commi(self, protezione_civile: Act) -> None:
        art1 = protezione_civile.articles[0]
        assert art1.heading is not None
        assert art1.heading.startswith(
            "Definizione e finalita' del Servizio nazionale della protezione civile"
        )
        assert [c.number for c in art1.commi] == ["1", "2", "3", "4"]
        assert art1.commi[0].text.startswith("1. Il Servizio nazionale della protezione civile")

    def test_capo_partition_in_path(self, protezione_civile: Act) -> None:
        assert protezione_civile.articles[0].path == [
            "CAPO I - Capo I Finalita', attivita' e composizione del Servizio nazionale "
            "della protezione civile"
        ]
        art50 = protezione_civile.articles[-1]
        assert art50.path == ["CAPO X - Capo VII Norme transitorie, di coordinamento e finali"]

    def test_note_sections_excluded_from_articles(self, protezione_civile: Act) -> None:
        # The "N O T E" block after art. 1 (GU redactional notes) is cut out.
        for article in protezione_civile.articles:
            for comma in article.commi:
                assert "Note alle premesse" not in comma.text

    def test_normattiva_double_parens_kept(self, protezione_civile: Act) -> None:
        # Art. 16, comma 2 ends with the consolidation marker ((17)) -- content, not stripped.
        art16 = next(a for a in protezione_civile.articles if a.number == "16")
        assert art16.heading is not None
        assert art16.heading.startswith("Tipologia dei rischi di protezione civile")
        assert [c.number for c in art16.commi] == ["1", "2", "3"]
        assert "((17))" in art16.commi[1].text

    def test_aggiornamento_block_stays_in_article(self, protezione_civile: Act) -> None:
        # The AGGIORNAMENTO (17) block after art. 16 comma 3 is kept in the article
        # body (the chunker separates it later, per A8).
        art16 = next(a for a in protezione_civile.articles if a.number == "16")
        assert "AGGIORNAMENTO (17)" in art16.commi[-1].text


# ---------------------------------------------------------------------------
# A4.2 - setext-h2 articles (short acts)
# ---------------------------------------------------------------------------


class TestSetextArticles:
    def test_duplice_uso_abrogato_inline(self) -> None:
        act = parse_act(DUPLICE_USO)
        assert act.title == "DECRETO LEGISLATIVO 24 febbraio 1997 n. 89"
        assert [a.number for a in act.articles] == [str(n) for n in range(1, 11)]
        for article in act.articles:
            assert article.heading is None
            assert article.path == []
            assert len(article.commi) == 1
            assert article.commi[0].number is None
            assert "((PROVVEDIMENTO ABROGATO DAL" in article.commi[0].text

    def test_vino_multiline_subtitle(self) -> None:
        act = parse_act(VINO)
        assert act.subtitle == (
            "Modalita' di consegna del vino in distilleria in "
            "applicazione dell'art. 1 del regolamento CEE n. 1410/87 per "
            "i produttori che nella campagna 1986-87 non hanno "
            "trasformato i mosti in mosti concentrati."
        )

    def test_vino_articles_and_spurious_h2(self) -> None:
        # "IL MINISTRO" and "Attesa" are setext h2 but NOT articles.
        act = parse_act(VINO)
        assert [a.number for a in act.articles] == ["1", "2"]
        assert "Il produttore vinicolo" in article_text(act, 0)
        assert "I volumi di vino" in article_text(act, 1)

    def test_vino_closing_formula_excluded(self) -> None:
        # The closing-formula h2 ("Il presente decreto, munito del sigillo...")
        # terminates the last article.
        act = parse_act(VINO)
        assert "munito del sigillo" not in article_text(act, 1)
        # But the plain closing line inside the article body stays.
        assert "sara' pubblicato nella Gazzetta Ufficiale" in article_text(act, 1)


# ---------------------------------------------------------------------------
# A4.3 - plain markers "<Titolo>-art. N [bis]" (Codice Penale, truncated at art. 85)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def codice_penale() -> Act:
    return parse_act(CODICE_PENALE)


class TestCodicePenale:
    def test_total_articles(self, codice_penale: Act) -> None:
        # 3 setext articles of the approval decree + 96 plain markers of the CP.
        assert len(codice_penale.articles) == 99
        assert sum(1 for a in codice_penale.articles if a.path == ["Codice Penale"]) == 96

    def test_decree_articles_first(self, codice_penale: Act) -> None:
        decree = codice_penale.articles[:3]
        assert [a.number for a in decree] == ["1", "2", "3"]
        assert all(a.path == [] for a in decree)
        assert "Il testo definitivo del" in decree[0].commi[0].text

    def test_cp_article_1_rubric(self, codice_penale: Act) -> None:
        art1 = codice_penale.articles[3]
        assert art1.number == "1"
        assert art1.path == ["Codice Penale"]
        assert art1.heading == "Reati e pene: disposizione espressa di legge"
        assert "Nessuno puo' essere punito" in "\n".join(c.text for c in art1.commi)

    def test_bis_ter_suffixes_normalized(self, codice_penale: Act) -> None:
        numbers = [a.number for a in codice_penale.articles]
        for expected in ("3-bis", "20-bis", "32-bis", "32-ter", "32-quater", "32-quinquies"):
            assert expected in numbers

    def test_truncation_boundary(self, codice_penale: Act) -> None:
        numbers = [a.number for a in codice_penale.articles]
        assert "84" in numbers
        assert "85" not in numbers
        assert codice_penale.articles[-1].number == "84"


# ---------------------------------------------------------------------------
# A5 - commi numbering with monotonicity check (Bilancio 2025, art. 1)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def bilancio() -> Act:
    return parse_act(BILANCIO)


class TestBilancioCommi:
    def test_single_article(self, bilancio: Act) -> None:
        assert len(bilancio.articles) == 1
        art1 = bilancio.articles[0]
        assert art1.number == "1"
        assert art1.heading is not None
        assert art1.heading.startswith("Risultati differenziali")
        assert art1.path == [
            "CAPO I PARTE I SEZIONE I: MISURE QUANTITATIVE PER LA REALIZZAZIONE "
            "DEGLI OBIETTIVI PROGRAMMATICI"
        ]

    def test_comma_count_and_boundaries(self, bilancio: Act) -> None:
        # Fixture holds commi 1-249 plus the consolidated insertions 48-bis and
        # 164-bis: 251 commi. (The task brief said 249; the file says 251 --
        # the two -bis commi are real line-start commi, reality wins.)
        numbers = [c.number for c in bilancio.articles[0].commi]
        assert len(numbers) == 251
        assert numbers[0] == "1"
        assert numbers[-1] == "249"
        assert "48-bis" in numbers
        assert "164-bis" in numbers

    def test_sub_list_items_are_not_commi(self, bilancio: Act) -> None:
        # Quoted amendment text inside a comma restarts numbering ("2.", "10-ter.",
        # ...): the monotonicity check must reject those lines.
        numbers = [c.number for c in bilancio.articles[0].commi]
        assert "10-ter" not in numbers
        assert numbers.count("2") == 1
        # The quoted "Art. 16-ter" block stays inside comma 10's text.
        comma10 = comma_by_number(bilancio, 0, "10")
        assert "Art. 16-ter" in comma10.text

    def test_commi_strictly_progressive(self, bilancio: Act) -> None:
        def key(number: str) -> tuple[int, str]:
            base, _, suffix = number.partition("-")
            return (int(base), suffix)

        seen = [key(c.number) for c in bilancio.articles[0].commi]
        for prev, cur in zip(seen, seen[1:]):
            assert cur[0] >= prev[0]


# ---------------------------------------------------------------------------
# A1 - base64 Akoma Ntoso HTML (Codice Civile, truncated after art. 2051)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def codice_civile() -> Act:
    return parse_act(CODICE_CIVILE)


class TestCodiceCivileAkn:
    def test_header(self, codice_civile: Act) -> None:
        assert codice_civile.title == "REGIO DECRETO 16 marzo 1942 n. 262"
        assert codice_civile.subtitle == "Approvazione del testo del Codice civile. (042U0262)"

    def test_article_count(self, codice_civile: Act) -> None:
        # 2 decree articles (article-num-akn) + 31 preleggi + 2141 CC articles
        # (attachment-name divs) = 2174.
        assert len(codice_civile.articles) == 2174
        assert sum(1 for a in codice_civile.articles if a.path == ["CODICE CIVILE"]) == 2141
        assert (
            sum(
                1
                for a in codice_civile.articles
                if a.path == ["Disposizioni sulla legge in generale"]
            )
            == 31
        )

    def test_decree_articles(self, codice_civile: Act) -> None:
        first = codice_civile.articles[0]
        assert first.number == "1"
        assert first.path == []
        text = "\n".join(c.text for c in first.commi)
        assert "E' approvato il testo del" in text
        # The art_aggiornamento-akn block following the decree article stays in it.
        assert "AGGIORNAMENTO (3)" in text

    def test_preleggi_article_1(self, codice_civile: Act) -> None:
        art = codice_civile.articles[2]
        assert art.number == "1"
        assert art.path == ["Disposizioni sulla legge in generale"]
        assert art.heading == "Indicazione delle fonti"
        assert "le leggi" in art.commi[0].text

    def test_abrogated_article_marker_kept(self, codice_civile: Act) -> None:
        art17 = next(
            a
            for a in codice_civile.articles
            if a.path == ["Disposizioni sulla legge in generale"] and a.number == "17"
        )
        assert "((ARTICOLO ABROGATO DALLA" in "\n".join(c.text for c in art17.commi)

    def test_art_2051_rubric(self, codice_civile: Act) -> None:
        art = next(
            a for a in codice_civile.articles if a.path == ["CODICE CIVILE"] and a.number == "2051"
        )
        assert art.heading == "Danno cagionato da cosa in custodia"
        assert "caso fortuito" in art.commi[0].text

    def test_truncation_boundary(self, codice_civile: Act) -> None:
        assert codice_civile.articles[-1].number == "2051"
        cc_numbers = [a.number for a in codice_civile.articles if a.path == ["CODICE CIVILE"]]
        assert "2052" not in cc_numbers

    def test_bis_suffix_normalized(self, codice_civile: Act) -> None:
        cc_numbers = {a.number for a in codice_civile.articles if a.path == ["CODICE CIVILE"]}
        assert "1117-bis" in cc_numbers
        assert "1469-sexies" in cc_numbers

    def test_slash_numbered_articles(self, codice_civile: Act) -> None:
        # Historical adoption articles "CODICE CIVILE-art. 314/2".."314/28".
        cc_numbers = {a.number for a in codice_civile.articles if a.path == ["CODICE CIVILE"]}
        assert "314/2" in cc_numbers
        assert "314/28" in cc_numbers


# ---------------------------------------------------------------------------
# A9/A10 - pathological fixtures
# ---------------------------------------------------------------------------


class TestPathological:
    def test_nul_padding_truncated(self) -> None:
        act = parse_act(REGIO_343)
        assert act.title == "REGIO DECRETO 11 aprile 1912 n. 343"
        assert [a.number for a in act.articles] == ["1"]
        text = article_text(act, 0)
        assert "PROVVEDIMENTO ABROGATO" in text
        assert "\x00" not in text

    def test_atx_heading_underlined_is_not_setext(self) -> None:
        # "### (012U0343)" followed by "----": the dashes are a separator, not a
        # setext underline; the ATX h3 is not an article and not the subtitle.
        act = parse_act(REGIO_343)
        assert act.subtitle is None

    def test_smallest_act_body_null_literal(self) -> None:
        act = parse_act(ANNULLAMENTO)
        assert act.title == "REGIO DECRETO 24 giugno 1920 n. 1371"
        assert act.subtitle == "Annullamento di partita (020U1371)"
        assert len(act.articles) == 1
        art = act.articles[0]
        assert art.number == "1"
        assert art.commi == [Comma(number=None, text="null")]

    def test_decreto_decaduto_body_as_is(self) -> None:
        act = parse_act(DECADUTO)
        assert act.subtitle == "Disposizioni urgenti in materia sanitaria."
        assert [a.number for a in act.articles] == ["1", "2", "3"]
        for article in act.articles:
            assert len(article.commi) == 1
            assert article.commi[0].number is None
            assert article.commi[0].text.strip() == "DECRETO DECADUTO"


# ---------------------------------------------------------------------------
# Synthetic edge cases (A2, A9, A10)
# ---------------------------------------------------------------------------


class TestSynthetic:
    def test_act_without_articles_gets_pseudo_article(self) -> None:
        text = (
            "REGIO DECRETO 1 gennaio 1900 n. 1\n"
            "=================================\n"
            "\n"
            "Un atto senza articoli (000U0001)\n"
            "---------------------------------\n"
            "\n"
            "Testo libero senza alcun marcatore di articolo.\n"
        )
        act = parse_act_text(text)
        assert len(act.articles) == 1
        assert act.articles[0].number == "unico"
        assert act.articles[0].commi[0].number is None
        assert "Testo libero" in act.articles[0].commi[0].text

    def test_nul_truncation_in_text(self) -> None:
        text = (
            "LEGGE 1 gennaio 2000 n. 1\n"
            "=========================\n"
            "\n"
            "Art. 1\n"
            "------\n"
            "\n"
            "corpo\x00### Art. 99\nfantasma\n"
        )
        act = parse_act_text(text)
        assert [a.number for a in act.articles] == ["1"]
        assert "fantasma" not in article_text(act, 0)

    def test_underline_after_blank_is_separator_not_heading(self) -> None:
        text = (
            "LEGGE 1 gennaio 2000 n. 1\n"
            "=========================\n"
            "\n"
            "Art. 1\n"
            "------\n"
            "\n"
            "testo del comma\n"
            "\n"
            "---------------\n"
            "\n"
            "AGGIORNAMENTO (1)\n"
            "\n"
            "nota di aggiornamento\n"
        )
        act = parse_act_text(text)
        # No spurious heading: the separator stays inside the article body.
        assert [a.number for a in act.articles] == ["1"]
        assert "AGGIORNAMENTO (1)" in article_text(act, 0)
        assert act.subtitle is None

    def test_articolo_unico_setext_marker(self) -> None:
        # Real-corpus phenomenon (conversion/ratification laws) not listed in
        # A4: "Articolo unico" underlined as setext h2 marks the only article.
        text = (
            "LEGGE 09 giugno 1950 n. 341\n"
            "===========================\n"
            "\n"
            "Ratifica con modificazioni del decreto legislativo 27 marzo 1948, n. 267.\n"
            "--------------------------------------------------------------------------\n"
            "\n"
            "PROMULGA\n"
            "--------\n"
            "\n"
            "la seguente legge:\n"
            "\n"
            "Articolo unico\n"
            "--------------\n"
            "\n"
            "Il decreto legislativo 27 marzo 1948, n. 267, e' ratificato.\n"
        )
        act = parse_act_text(text)
        assert [a.number for a in act.articles] == ["unico"]
        assert "ratificato" in act.articles[0].commi[0].text

    def test_article_without_numbered_commi_single_comma(self) -> None:
        text = (
            "LEGGE 1 gennaio 2000 n. 1\n"
            "=========================\n"
            "\n"
            "Art. 1\n"
            "------\n"
            "\n"
            "Primo capoverso senza numero.\n"
            "Secondo capoverso senza numero.\n"
        )
        act = parse_act_text(text)
        commi = act.articles[0].commi
        assert len(commi) == 1
        assert commi[0].number is None
        assert "Primo capoverso" in commi[0].text


# ---------------------------------------------------------------------------
# Failure contract: degenerate inputs never raise, always yield >= 1 article
# ---------------------------------------------------------------------------


def assert_sane_pseudo_act(act: Act) -> None:
    assert isinstance(act, Act)
    assert len(act.articles) == 1
    assert act.articles[0].number == "unico"
    assert len(act.articles[0].commi) == 1


class TestDegenerateInputs:
    def test_empty_text(self) -> None:
        assert_sane_pseudo_act(parse_act_text(""))

    def test_nul_only_file(self, tmp_path: Path) -> None:
        path = tmp_path / "nul.md"
        path.write_bytes(b"\x00" * 64)
        assert_sane_pseudo_act(parse_act(path))

    def test_binary_garbage_file(self, tmp_path: Path) -> None:
        # Invalid UTF-8 sequences (no NUL, so nothing is truncated away).
        path = tmp_path / "garbage.md"
        path.write_bytes(b"\xc3\x28\xff\xfe\x80\x81\xf0\x90\x28\xbc" * 32)
        act = parse_act(path)
        assert_sane_pseudo_act(act)

    def test_corrupt_base64_falls_back_to_markdown(self) -> None:
        # Starts like base64 "<html" but the payload has invalid characters;
        # b64decode *discards* them, so the mod-4 pre-truncation cannot save
        # the decode (binascii.Error). The defined behavior is to degrade to
        # the markdown path: garbage -> pseudo-article "unico", never raise.
        text = "PGh0bWw!!!???***" + "@" * 37
        act = parse_act_text(text)
        assert_sane_pseudo_act(act)
        assert act.source_format == "markdown"
        assert "PGh0bWw" in act.articles[0].commi[0].text

    def test_corrupt_base64_file(self, tmp_path: Path) -> None:
        path = tmp_path / "corrupt_akn.md"
        path.write_bytes(b"PGh0bWw\xc3\x28!!!not-base64-anymore***")
        assert_sane_pseudo_act(parse_act(path))


# ---------------------------------------------------------------------------
# AKN: unmatched article-num-akn markers are kept verbatim and logged
# ---------------------------------------------------------------------------


class TestAknUnmatchedArtnum:
    def test_warning_logged_and_raw_number_kept(self, caplog: pytest.LogCaptureFixture) -> None:
        html = (
            '<html><body><h2 class="article-num-akn">Articolo strano</h2>'
            '<span class="art-just-text-akn">testo del corpo</span></body></html>'
        )
        with caplog.at_level("WARNING", logger="legger.corpus.akn"):
            act = parse_akn_html(html)
        assert [a.number for a in act.articles] == ["Articolo strano"]
        assert any(
            "article-num-akn" in record.message and "Articolo strano" in record.getMessage()
            for record in caplog.records
        )

    def test_no_warning_for_regular_marker(self, caplog: pytest.LogCaptureFixture) -> None:
        html = (
            '<html><body><h2 class="article-num-akn">Art. 3 bis</h2>'
            '<span class="art-just-text-akn">testo</span></body></html>'
        )
        with caplog.at_level("WARNING", logger="legger.corpus.akn"):
            act = parse_akn_html(html)
        assert [a.number for a in act.articles] == ["3-bis"]
        assert not caplog.records
