"""Tests for the incremental citation-marker parser (Task F2).

Table-driven: each case feeds a sequence of deltas (simulating how the
Anthropic stream slices the text arbitrarily) and asserts the exact pieces
out. The invariant test re-checks every case for byte-exactness: the
concatenation of all piece payloads equals the concatenation of the input.
"""

import pytest

from legger.chat.stream import (
    MARKER_BUFFER_CAP,
    MarkerParser,
    MarkerPiece,
    TextPiece,
    parse_marker,
)


def run_parser(deltas: list[str]) -> list[TextPiece | MarkerPiece]:
    parser = MarkerParser()
    pieces: list[TextPiece | MarkerPiece] = []
    for delta in deltas:
        pieces.extend(parser.feed(delta))
    pieces.extend(parser.flush())
    return pieces


CASES = {
    "plain_text_no_markers": (
        ["L'art. 2051 ", "disciplina la custodia."],
        [TextPiece("L'art. 2051 "), TextPiece("disciplina la custodia.")],
    ),
    "single_marker_one_delta": (
        ["Vedi [[codice-civile|art.2051]] in tema."],
        [
            TextPiece("Vedi "),
            MarkerPiece("[[codice-civile|art.2051]]"),
            TextPiece(" in tema."),
        ],
    ),
    "marker_split_across_three_deltas": (
        ["Vedi [[codice-", "civile|art.20", "51]] fine."],
        [
            TextPiece("Vedi "),
            MarkerPiece("[[codice-civile|art.2051]]"),
            TextPiece(" fine."),
        ],
    ),
    "open_bracket_split_across_deltas": (
        # "[[" itself split: "[" at the end of a delta must be held back.
        ["testo [", "[codice-civile|art.1]] coda"],
        [
            TextPiece("testo "),
            MarkerPiece("[[codice-civile|art.1]]"),
            TextPiece(" coda"),
        ],
    ),
    "close_bracket_split_across_deltas": (
        ["[[codice-civile|art.1]", "] coda"],
        [MarkerPiece("[[codice-civile|art.1]]"), TextPiece(" coda")],
    ),
    "single_bracket_false_alarm": (
        # A lone "[" not followed by another "[" is plain text.
        ["lettera a", "[1] del comma"],
        [TextPiece("lettera a"), TextPiece("[1] del comma")],
    ),
    "trailing_single_bracket_flushed": (
        ["finisce con ["],
        [TextPiece("finisce con "), TextPiece("[")],
    ),
    "unterminated_marker_flushed_as_text": (
        ["inizio [[codice-civile|art.2051 senza chiusura"],
        [
            TextPiece("inizio "),
            TextPiece("[[codice-civile|art.2051 senza chiusura"),
        ],
    ),
    "multiple_markers_one_delta": (
        ["[[codice-civile|art.2051]] e [[codice-penale|art.52|c.2]]."],
        [
            MarkerPiece("[[codice-civile|art.2051]]"),
            TextPiece(" e "),
            MarkerPiece("[[codice-penale|art.52|c.2]]"),
            TextPiece("."),
        ],
    ),
    "marker_with_comma_half": (
        ["[[dlgs-285-1992|art.186|c.2]]"],
        [MarkerPiece("[[dlgs-285-1992|art.186|c.2]]")],
    ),
    "empty_deltas_ignored": (
        ["", "testo", ""],
        [TextPiece("testo")],
    ),
}


@pytest.mark.parametrize("deltas,expected", CASES.values(), ids=CASES.keys())
def test_parser_pieces(deltas: list[str], expected: list) -> None:
    assert run_parser(deltas) == expected


@pytest.mark.parametrize("deltas,expected", CASES.values(), ids=CASES.keys())
def test_parser_is_byte_exact(deltas: list[str], expected: list) -> None:
    pieces = run_parser(deltas)
    joined = "".join(p.raw if isinstance(p, MarkerPiece) else p.text for p in pieces)
    assert joined == "".join(deltas)


def test_buffer_cap_flushes_runaway_marker_as_text() -> None:
    """An unterminated '[[' stops buffering after MARKER_BUFFER_CAP chars.

    The pent-up buffer comes back out as plain text (byte-exact), and a
    later REAL marker is still recognized.
    """
    runaway = "[[" + "x" * (MARKER_BUFFER_CAP + 50)
    deltas = [runaway, " poi [[codice-civile|art.1]]"]
    pieces = run_parser(deltas)
    assert MarkerPiece("[[codice-civile|art.1]]") in pieces
    assert all(isinstance(p, TextPiece) for p in pieces[:-1])
    joined = "".join(p.raw if isinstance(p, MarkerPiece) else p.text for p in pieces)
    assert joined == "".join(deltas)


def test_buffer_cap_recovers_inner_marker() -> None:
    """A real marker hiding inside a runaway buffer is recovered after the cap."""
    filler = "y" * (MARKER_BUFFER_CAP + 10)
    text = f"[[{filler}[[codice-civile|art.7]] coda"
    pieces = run_parser([text])
    assert MarkerPiece("[[codice-civile|art.7]]") in pieces
    joined = "".join(p.raw if isinstance(p, MarkerPiece) else p.text for p in pieces)
    assert joined == text


# --- parse_marker -------------------------------------------------------------


def test_parse_marker_with_comma() -> None:
    parsed = parse_marker("[[dlgs-285-1992|art.186|c.2]]")
    assert parsed is not None
    assert parsed.act_ref == "dlgs-285-1992"
    assert parsed.article == "186"
    assert parsed.comma == "2"


def test_parse_marker_without_comma() -> None:
    parsed = parse_marker("[[codice-civile|art.2051]]")
    assert parsed is not None
    assert parsed.act_ref == "codice-civile"
    assert parsed.article == "2051"
    assert parsed.comma is None


def test_parse_marker_suffixed_article() -> None:
    parsed = parse_marker("[[codice-penale|art.613-bis]]")
    assert parsed is not None
    assert parsed.article == "613-bis"


def test_parse_marker_malformed_returns_none() -> None:
    assert parse_marker("[[solo testo libero]]") is None
    assert parse_marker("[[codice-civile]]") is None
    assert parse_marker("[[CODICE|art.1]]") is None  # act_ref slugs are lowercase
