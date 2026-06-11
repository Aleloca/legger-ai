"""Tests for the citation guardrail (Task F3) — pure functions, no I/O.

:func:`legger.chat.guardrail.check_citation` verifies a parsed marker
against the retrieval hits. The contract under test:

- act_ref not among the hits  -> ``act_not_in_context`` (verified False)
- article not among that act's hits -> ``article_not_in_context`` (False)
- act+article found, marker comma missing from the matching hits' commi
  lists -> ``comma_not_in_context`` BUT verified stays True (advisory:
  commi lists are empty for unnumbered text, and split chunks may not
  cover every comma — a hard fail would raise false alarms)
- full match (or no comma in the marker) -> ``ok`` (True)
- the returned ``hit`` enriches the citation (title/vigenza): the hit
  whose commi contain the marker comma wins, else the first act+article
  match; None when act/article verification fails.
"""

from legger.chat.guardrail import check_citation
from legger.chat.stream import ParsedMarker, parse_marker
from legger.retrieval.search import SearchHit


def make_hit(
    act_ref: str = "codice-civile",
    article: str = "2051",
    commi: list[str] | None = None,
    chunk_id: str | None = None,
    act_title: str = "Codice civile",
) -> SearchHit:
    return SearchHit(
        score=0.9,
        chunk_id=chunk_id if chunk_id is not None else f"{act_ref}#art-{article}#0",
        act_ref=act_ref,
        article=article,
        act_title=act_title,
        header=f"{act_title}\nArt. {article}",
        text="testo",
        vigenza="vigente",
        payload={"commi": commi if commi is not None else []},
    )


def marker(act_ref: str = "codice-civile", article: str = "2051", comma: str | None = None):
    return ParsedMarker(act_ref=act_ref, article=article, comma=comma)


# --- ok ---------------------------------------------------------------------


def test_ok_without_comma() -> None:
    hit = make_hit()
    check = check_citation(marker(), [hit])
    assert check.verified is True
    assert check.reason == "ok"
    assert check.hit is hit


def test_ok_with_comma_in_commi() -> None:
    hit = make_hit(commi=["1", "2"])
    check = check_citation(marker(comma="2"), [hit])
    assert check.verified is True
    assert check.reason == "ok"
    assert check.hit is hit


def test_ok_no_comma_multiple_hits_returns_first_match() -> None:
    other = make_hit(act_ref="codice-penale", article="52")
    first = make_hit(chunk_id="codice-civile#art-2051#0")
    second = make_hit(chunk_id="codice-civile#art-2051#1")
    check = check_citation(marker(), [other, first, second])
    assert check.reason == "ok"
    assert check.hit is first


# --- act_not_in_context -----------------------------------------------------


def test_act_not_in_context() -> None:
    check = check_citation(marker(act_ref="codice-penale"), [make_hit()])
    assert check.verified is False
    assert check.reason == "act_not_in_context"
    assert check.hit is None


def test_empty_hits_is_act_not_in_context() -> None:
    check = check_citation(marker(), [])
    assert check.verified is False
    assert check.reason == "act_not_in_context"
    assert check.hit is None


# --- article_not_in_context --------------------------------------------------


def test_article_not_in_context() -> None:
    check = check_citation(marker(article="1218"), [make_hit(article="2051")])
    assert check.verified is False
    assert check.reason == "article_not_in_context"
    assert check.hit is None


def test_article_under_other_act_does_not_count() -> None:
    # Article 52 was retrieved, but for the *penale* act: citing
    # codice-civile art. 52 must not pass on the strength of another act.
    hits = [make_hit(act_ref="codice-civile", article="2051"), make_hit("codice-penale", "52")]
    check = check_citation(marker(act_ref="codice-civile", article="52"), hits)
    assert check.verified is False
    assert check.reason == "article_not_in_context"


# --- comma_not_in_context (advisory: verified stays True) ---------------------


def test_comma_not_in_context_is_verified_anyway() -> None:
    hit = make_hit(commi=["1", "2"])
    check = check_citation(marker(comma="3"), [hit])
    assert check.verified is True  # advisory only
    assert check.reason == "comma_not_in_context"
    assert check.hit is hit  # enrichment still uses the article match


def test_empty_commi_list_is_advisory_not_a_failure() -> None:
    # Unnumbered text (historical acts) indexes with commi == []: the comma
    # cannot be confirmed, but the act+article match keeps verified True.
    hit = make_hit(commi=[])
    check = check_citation(marker(comma="1"), [hit])
    assert check.verified is True
    assert check.reason == "comma_not_in_context"
    assert check.hit is hit


def test_comma_advisory_returns_first_article_match() -> None:
    first = make_hit(commi=["1"], chunk_id="codice-civile#art-2051#0")
    second = make_hit(commi=["2"], chunk_id="codice-civile#art-2051#1")
    check = check_citation(marker(comma="9"), [first, second])
    assert check.reason == "comma_not_in_context"
    assert check.hit is first


# --- split chunks: the comma may live in a later hit ---------------------------


def test_comma_found_in_second_hit_of_split_article() -> None:
    # An article split across chunks yields multiple hits with disjoint
    # commi lists; the guardrail must scan ALL act+article matches.
    first = make_hit(commi=["1", "2"], chunk_id="codice-civile#art-2051#0")
    second = make_hit(commi=["2", "3"], chunk_id="codice-civile#art-2051#1")
    check = check_citation(marker(comma="3"), [first, second])
    assert check.verified is True
    assert check.reason == "ok"
    assert check.hit is second  # enrichment prefers the comma-bearing hit


# --- suffixed numbers and normalization ----------------------------------------


def test_suffixed_article_matches() -> None:
    hit = make_hit(article="62-bis")
    check = check_citation(marker(article="62-bis"), [hit])
    assert check.reason == "ok"


def test_suffixed_comma_matches() -> None:
    hit = make_hit(commi=["2-sexies"])
    check = check_citation(marker(comma="2-sexies"), [hit])
    assert check.reason == "ok"


def test_matching_is_case_insensitive() -> None:
    # String equality after lowercase normalization: "62-BIS" == "62-bis".
    hit = make_hit(article="62-BIS", commi=["2-Sexies"])
    check = check_citation(marker(article="62-bis", comma="2-sexies"), [hit])
    assert check.reason == "ok"


def test_suffixed_article_is_not_its_base_number() -> None:
    check = check_citation(marker(article="62"), [make_hit(article="62-bis")])
    assert check.verified is False
    assert check.reason == "article_not_in_context"


# --- precondition: only parser-accepted markers reach the guardrail ------------


def test_malformed_markers_are_rejected_upstream() -> None:
    # check_citation's precondition (documented in its docstring): the
    # marker comes from parse_marker, which rejects anything outside the
    # contract format — so malformed input never reaches the guardrail.
    assert parse_marker("[[non un marker]]") is None
    assert parse_marker("[[codice-civile|art.2051|c.1|extra]]") is None
