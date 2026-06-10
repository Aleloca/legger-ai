"""Tests for the article-based chunker (Task B5), driven by real fixtures.

Expected values were read by hand from the parsed fixtures (see
``tests/fixtures/README.md``): protezione civile art. 18 has 4 commi and a
rubrica; the legge di bilancio fixture has a single art. 1 with 251 numbered
commi (split + 1-comma overlap path); the Codice Civile AKN fixture has
art. 2051 with an unnumbered comma plus three distinct articles numbered "1"
(id disambiguation path); the Codice Penale art. 69 is a single unnumbered
19k-char comma (paragraph-fallback split path).
"""

from pathlib import Path

import pytest

from legger.corpus.chunker import (
    MAX_BODY,
    MAX_HEADER,
    MAX_TEXT,
    Chunk,
    chunk_act,
)
from legger.corpus.models import Act, Article, Comma
from legger.corpus.parser import parse_act
from legger.corpus.refs import ActRef, derive_act_ref, vigenza_from_path

FIXTURES = Path(__file__).parent / "fixtures" / "corpus"

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


def chunk_fixture(rel_path: str) -> tuple[Act, list[Chunk]]:
    act = parse_act(FIXTURES / rel_path)
    ref = derive_act_ref(act, rel_path)
    chunks = chunk_act(
        act,
        ref,
        vigenza=vigenza_from_path(rel_path),
        collection=rel_path.split("/", 1)[0],
        file_path=rel_path,
    )
    return act, chunks


def by_article(chunks: list[Chunk], number: str) -> list[Chunk]:
    return [c for c in chunks if c.article == number]


def chunk_synthetic(article: Article) -> list[Chunk]:
    """Chunk a hand-built single-article act (for edge-branch tests)."""
    act = Act(title="LEGGE 1 gennaio 2000 n. 1", articles=[article])
    ref = ActRef(act_ref="legge-1-2000", act_type="legge", number="1", year=2000, source="header")
    return chunk_act(act, ref, vigenza="vigente", collection="X", file_path="X/y.md")


def body_of(chunk: Chunk) -> str:
    return chunk.text.removeprefix(chunk.header + "\n\n")


# ---------------------------------------------------------------------------
# Mid-size article: 1 article = 1 chunk (protezione civile art. 18)
# ---------------------------------------------------------------------------


class TestSingleChunkArticle:
    @pytest.fixture(scope="class")
    def chunks(self) -> list[Chunk]:
        return chunk_fixture(PROTEZIONE_CIVILE)[1]

    def test_exactly_one_chunk(self, chunks: list[Chunk]) -> None:
        assert len(by_article(chunks, "18")) == 1

    def test_header_first_line_is_estremi_and_title(self, chunks: list[Chunk]) -> None:
        (chunk,) = by_article(chunks, "18")
        line1 = chunk.header.splitlines()[0]
        # Known-codici precedence (refs.py step 0): the act_type is "codice",
        # so the estremi line is labelled "Codice", with the carrier numero.
        assert line1 == "Codice 1/2018 — Codice della protezione civile."

    def test_header_has_rubrica_without_source_refs(self, chunks: list[Chunk]) -> None:
        (chunk,) = by_article(chunks, "18")
        # The raw rubrica carries a long "(Articolo 3, commi ...)" source
        # reference full of markdown links: the header must strip it.
        assert "Art. 18 — Pianificazione di protezione civile" in chunk.header.splitlines()
        assert "http" not in chunk.header

    def test_header_has_partition_path(self, chunks: list[Chunk]) -> None:
        (chunk,) = by_article(chunks, "18")
        assert "CAPO V" in chunk.header

    def test_no_comma_range_when_not_split(self, chunks: list[Chunk]) -> None:
        (chunk,) = by_article(chunks, "18")
        assert "(commi" not in chunk.header
        assert "(parte" not in chunk.header

    def test_text_is_header_plus_body(self, chunks: list[Chunk]) -> None:
        (chunk,) = by_article(chunks, "18")
        assert chunk.text.startswith(chunk.header + "\n\n")
        # Comma text keeps the "N. " prefix (B3), so the body reads naturally.
        assert "1. " in chunk.text

    def test_commi_payload(self, chunks: list[Chunk]) -> None:
        (chunk,) = by_article(chunks, "18")
        assert chunk.commi == ["1", "2", "3", "4"]

    def test_chunk_id(self, chunks: list[Chunk]) -> None:
        (chunk,) = by_article(chunks, "18")
        assert chunk.id == "codice-protezione-civile#art-18#0"

    def test_payload_fields(self, chunks: list[Chunk]) -> None:
        (chunk,) = by_article(chunks, "18")
        assert chunk.act_ref == "codice-protezione-civile"
        assert chunk.act_type == "codice"
        assert chunk.act_title == "Codice della protezione civile."
        assert chunk.collection == "Codici"
        assert chunk.vigenza == "vigente"
        assert chunk.file_path == PROTEZIONE_CIVILE


# ---------------------------------------------------------------------------
# Articolo-fiume: split into comma groups with 1-comma overlap (bilancio)
# ---------------------------------------------------------------------------


class TestBilancioSplit:
    @pytest.fixture(scope="class")
    def data(self) -> tuple[Act, list[Chunk]]:
        return chunk_fixture(BILANCIO)

    def test_more_than_one_chunk(self, data: tuple[Act, list[Chunk]]) -> None:
        _, chunks = data
        assert len(by_article(chunks, "1")) > 1

    def test_consecutive_chunks_overlap_by_exactly_one_comma(
        self, data: tuple[Act, list[Chunk]]
    ) -> None:
        _, chunks = data
        art1 = by_article(chunks, "1")
        for left, right in zip(art1, art1[1:]):
            assert right.commi[0] == left.commi[-1]
            assert set(left.commi) & set(right.commi) == {left.commi[-1]}

    def test_headers_carry_comma_ranges(self, data: tuple[Act, list[Chunk]]) -> None:
        _, chunks = data
        for chunk in by_article(chunks, "1"):
            marker = f"(commi {chunk.commi[0]}–{chunk.commi[-1]})"
            assert marker in chunk.header

    def test_union_of_commi_covers_all_251(self, data: tuple[Act, list[Chunk]]) -> None:
        act, chunks = data
        expected = {c.number for c in act.articles[0].commi if c.number is not None}
        assert len(expected) == 251
        covered: set[str] = set()
        for chunk in by_article(chunks, "1"):
            covered.update(chunk.commi)
        assert covered == expected

    def test_no_chunk_exceeds_cap(self, data: tuple[Act, list[Chunk]]) -> None:
        _, chunks = data
        for c in chunks:
            assert len(c.text) <= MAX_TEXT, c.id

    def test_split_ids_are_sequential(self, data: tuple[Act, list[Chunk]]) -> None:
        _, chunks = data
        art1 = by_article(chunks, "1")
        assert [c.id for c in art1] == [f"legge-207-2024#art-1#{i}" for i in range(len(art1))]


# ---------------------------------------------------------------------------
# Codice Civile (AKN): attachment path in header, unnumbered comma
# ---------------------------------------------------------------------------


class TestCodiceCivile:
    @pytest.fixture(scope="class")
    def chunks(self) -> list[Chunk]:
        return chunk_fixture(CODICE_CIVILE)[1]

    def test_art_2051_single_chunk(self, chunks: list[Chunk]) -> None:
        assert len(by_article(chunks, "2051")) == 1

    def test_art_2051_header(self, chunks: list[Chunk]) -> None:
        (chunk,) = by_article(chunks, "2051")
        lines = chunk.header.splitlines()
        assert lines[0] == "Codice 262/1942 — Approvazione del testo del Codice civile."
        assert "CODICE CIVILE" in lines  # attachment name on its own line
        assert "Art. 2051 — Danno cagionato da cosa in custodia" in lines

    def test_art_2051_unnumbered_comma_payload(self, chunks: list[Chunk]) -> None:
        # A single unnumbered comma yields no comma-level citation targets:
        # the commi payload is empty and the citation falls back to the article.
        (chunk,) = by_article(chunks, "2051")
        assert chunk.commi == []
        assert chunk.text.startswith(chunk.header + "\n\n")

    def test_duplicate_article_numbers_get_disambiguated_ids(self, chunks: list[Chunk]) -> None:
        # The CC file holds three articles numbered "1" (approval decree,
        # preleggi, CODICE CIVILE attachment): the 2nd+ occurrences carry a
        # ".{occurrence}" disambiguator so ids stay unique act-wide.
        ones = by_article(chunks, "1")
        assert len(ones) == 3
        assert {c.id for c in ones} == {
            "codice-civile#art-1#0",
            "codice-civile#art-1.2#0",
            "codice-civile#art-1.3#0",
        }


# ---------------------------------------------------------------------------
# Paragraph-fallback split: single huge unnumbered comma (CP art. 69)
# ---------------------------------------------------------------------------


class TestParagraphFallback:
    @pytest.fixture(scope="class")
    def chunks(self) -> list[Chunk]:
        return chunk_fixture(CODICE_PENALE)[1]

    def test_split_into_parts(self, chunks: list[Chunk]) -> None:
        parts = by_article(chunks, "69")
        assert len(parts) >= 2
        assert all(len(c.text) <= MAX_TEXT for c in parts)

    def test_part_markers_and_empty_commi(self, chunks: list[Chunk]) -> None:
        parts = by_article(chunks, "69")
        total = len(parts)
        for k, chunk in enumerate(parts, start=1):
            assert f"(parte {k}/{total})" in chunk.header
            assert chunk.commi == []

    def test_no_mid_line_cuts(self, chunks: list[Chunk]) -> None:
        # Paragraph-boundary fallback: every part starts and ends on a line
        # that exists verbatim in the parsed article (never inside a line).
        act = parse_act(FIXTURES / CODICE_PENALE)
        art69 = next(a for a in act.articles if a.number == "69")
        original_lines = {line for line in art69.commi[0].text.splitlines() if line.strip()}
        for chunk in by_article(chunks, "69"):
            body_lines = chunk.text.removeprefix(chunk.header + "\n\n").splitlines()
            assert body_lines[0] in original_lines
            assert body_lines[-1] in original_lines


# ---------------------------------------------------------------------------
# Tiny acts and pseudo-article "unico"
# ---------------------------------------------------------------------------


class TestTinyActs:
    def test_smallest_act_single_chunk(self) -> None:
        _, chunks = chunk_fixture(ANNULLAMENTO)
        assert len(chunks) == 1
        (chunk,) = chunks
        assert chunk.header.startswith("Regio Decreto 1371/1920")
        assert chunk.vigenza == "abrogato"
        assert chunk.collection == "Atti normativi abrogati (in originale)"

    @pytest.mark.parametrize("number", ["unico", "Articolo unico", "Articolo unico."])
    def test_articolo_unico_header(self, number: str) -> None:
        # "unico" is the markdown pseudo-article; the AKN path can also emit
        # the raw "Articolo unico" heading as the article number.
        act = Act(
            title=None,
            articles=[Article(number=number, commi=[Comma(number=None, text="Testo breve.")])],
        )
        ref = ActRef(act_ref="dm-prova", act_type="decreto_ministeriale", source="filename")
        chunks = chunk_act(act, ref, vigenza="vigente", collection="DPCM", file_path="DPCM/x.md")
        assert len(chunks) == 1
        assert "Articolo unico" in chunks[0].header
        assert "Art. " not in chunks[0].header


# ---------------------------------------------------------------------------
# Header bounds and degenerate inputs
# ---------------------------------------------------------------------------


class TestHeaderBounds:
    def test_long_title_truncated_at_word_boundary(self) -> None:
        act = Act(
            title="LEGGE 30 dicembre 2024 n. 207",
            subtitle="Disposizioni varie " * 40,
            articles=[Article(number="1", commi=[Comma(number="1", text="1. Testo.")])],
        )
        ref = ActRef(
            act_ref="legge-207-2024", act_type="legge", number="207", year=2024, source="header"
        )
        chunks = chunk_act(act, ref, vigenza="vigente", collection="X", file_path="X/y.md")
        header = chunks[0].header
        assert len(header) <= MAX_HEADER
        assert "…" in header

    def test_codice_without_estremi_uses_title_only(self) -> None:
        act = Act(
            title="Codice Civile",
            articles=[Article(number="2051", commi=[Comma(number=None, text="Testo.")])],
        )
        ref = ActRef(act_ref="codice-civile", act_type="codice", source="header")
        chunks = chunk_act(act, ref, vigenza="vigente", collection="Codici", file_path="C/cc.md")
        assert chunks[0].header.splitlines()[0] == "Codice Civile"

    def test_single_huge_line_is_hard_capped(self) -> None:
        # No commi, no paragraph or line boundaries: last-resort hard slices.
        act = Act(
            title="LEGGE 1 gennaio 2000 n. 1",
            articles=[Article(number="1", commi=[Comma(number=None, text="x" * 30000)])],
        )
        ref = ActRef(
            act_ref="legge-1-2000", act_type="legge", number="1", year=2000, source="header"
        )
        chunks = chunk_act(act, ref, vigenza="vigente", collection="X", file_path="X/y.md")
        assert len(chunks) >= 4
        assert all(len(c.text) <= MAX_TEXT for c in chunks)

    def test_raw_heading_as_number_keeps_caps(self) -> None:
        # Failure contract: the AKN parser can emit an arbitrary-length raw
        # heading as ``article.number``. Its header contribution must be
        # budget-truncated so MAX_HEADER (and, with a MAX_BODY-sized body,
        # MAX_TEXT) still holds.
        number = ("Disposizioni transitorie " * 8).strip()  # ~200 chars
        article = Article(number=number, commi=[Comma(number=None, text="y" * 7600)])
        chunks = chunk_synthetic(article)
        assert chunks
        for c in chunks:
            assert len(c.header) <= MAX_HEADER, c.id
            assert len(c.text) <= MAX_TEXT, c.id


# ---------------------------------------------------------------------------
# Synthetic edge branches of the grouping/splitting machinery
# ---------------------------------------------------------------------------


class TestGroupEdgeBranches:
    def test_second_segment_allowance_avoids_stranded_chunk(self) -> None:
        # Sizes [100, 7000, 100]: greedy fill stops at TARGET_BODY, but a
        # group may always take a *second* segment up to MAX_BODY, so the
        # oversized middle comma joins both its neighbors instead of
        # stranding a duplicate single-segment chunk.
        article = Article(
            number="1",
            commi=[
                Comma(number="1", text="a" * 100),
                Comma(number="2", text="b" * 7000),
                Comma(number="3", text="c" * 100),
            ],
        )
        chunks = chunk_synthetic(article)
        assert [c.commi for c in chunks] == [["1", "2"], ["2", "3"]]
        for c in chunks:
            assert len(c.text) <= MAX_TEXT, c.id
            assert len(body_of(c)) <= MAX_BODY, c.id

    def test_adjacent_giant_commi_drop_the_overlap(self) -> None:
        # Two adjacent ~7000-char commi: carrying the 1-comma overlap would
        # exceed MAX_BODY, so it is dropped at that boundary. Coverage stays
        # complete and the chunks do not overlap.
        article = Article(
            number="1",
            commi=[
                Comma(number="1", text="a" * 7000),
                Comma(number="2", text="b" * 7000),
            ],
        )
        chunks = chunk_synthetic(article)
        assert [c.commi for c in chunks] == [["1"], ["2"]]  # no overlap
        for c in chunks:
            assert len(c.text) <= MAX_TEXT, c.id
            assert len(body_of(c)) <= MAX_BODY, c.id
        assert {n for c in chunks for n in c.commi} == {"1", "2"}

    def test_giant_paragraph_splits_at_line_boundaries(self) -> None:
        # Middle fallback rung: a single > MAX_BODY paragraph with no blank
        # lines is split at line boundaries (never mid-line, no hard slices).
        lines = [f"riga {i:03d} " + "x" * 90 for i in range(120)]  # ~12k chars
        article = Article(number="1", commi=[Comma(number=None, text="\n".join(lines))])
        chunks = chunk_synthetic(article)
        assert len(chunks) >= 2
        original = set(lines)
        for c in chunks:
            assert len(c.text) <= MAX_TEXT, c.id
            for line in body_of(c).splitlines():
                assert not line or line in original, c.id  # no mid-line cuts
        # Line-derived units rejoin with the single newline they had in the
        # source, not an inflated blank line.
        assert f"{lines[0]}\n{lines[1]}" in chunks[0].text


# ---------------------------------------------------------------------------
# Determinism and end-to-end smoke over all 9 fixtures
# ---------------------------------------------------------------------------


class TestDeterminismAndSmoke:
    @pytest.mark.parametrize("rel_path", ALL_FIXTURES)
    def test_two_runs_identical(self, rel_path: str) -> None:
        _, first = chunk_fixture(rel_path)
        _, second = chunk_fixture(rel_path)
        assert [(c.id, c.text) for c in first] == [(c.id, c.text) for c in second]

    @pytest.mark.parametrize("rel_path", ALL_FIXTURES)
    def test_smoke(self, rel_path: str) -> None:
        act, chunks = chunk_fixture(rel_path)
        assert len(chunks) >= len(act.articles)  # every article yields >= 1 chunk
        ids = [c.id for c in chunks]
        assert len(ids) == len(set(ids))  # unique act-wide
        for chunk in chunks:
            assert len(chunk.text) <= MAX_TEXT, chunk.id
            assert len(chunk.header) <= MAX_HEADER, chunk.id
            assert chunk.text.startswith(chunk.header), chunk.id
            assert chunk.id.startswith(f"{chunk.act_ref}#art-"), chunk.id
