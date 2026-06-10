"""Unit tests for legger.eval_retrieval (injected search fn, no Qdrant/embedder).

Pins down the hit/miss judgement (act_ref AND article, plain string equality —
"2-bis" is not "2"), the metric math (recall@5/@10, MRR), the per-kind
breakdown, the miss list rendering and the JSON report file.
"""

import json
from pathlib import Path

from legger.eval_retrieval import (
    QUERIES_PATH,
    EvalQuery,
    correct_rank,
    evaluate,
    format_report,
    load_queries,
    write_json_report,
)
from legger.retrieval.search import SearchHit


def make_hit(
    act_ref: str, article: str, header: str = "Atto\nArt. X", score: float = 0.5
) -> SearchHit:
    return SearchHit(
        score=score,
        chunk_id=f"{act_ref}#art-{article}#0",
        act_ref=act_ref,
        article=article,
        act_title=act_ref,
        header=header,
        text="...",
        vigenza="vigente",
        payload={"act_ref": act_ref, "article": article},
    )


def make_query(
    qid: str, kind: str = "explicit", act_ref: str = "codice-civile", article: str = "2051"
) -> EvalQuery:
    return EvalQuery(
        id=qid,
        query=f"query {qid}",
        kind=kind,
        expected_act_ref=act_ref,
        expected_article=article,
    )


# --- correct_rank -------------------------------------------------------------


def test_rank_of_first_correct_hit() -> None:
    hits = [
        make_hit("codice-penale", "52"),
        make_hit("codice-civile", "2051"),
        make_hit("codice-civile", "2051"),
    ]
    assert correct_rank(hits, "codice-civile", "2051") == 2


def test_both_fields_must_match() -> None:
    hits = [
        make_hit("codice-penale", "2051"),  # right article, wrong act
        make_hit("codice-civile", "52"),  # right act, wrong article
    ]
    assert correct_rank(hits, "codice-civile", "2051") is None


def test_article_match_is_string_equality_bis_is_not_base() -> None:
    hits = [make_hit("codice-penale", "62")]
    assert correct_rank(hits, "codice-penale", "62-bis") is None
    assert correct_rank([make_hit("codice-penale", "62-bis")], "codice-penale", "62-bis") == 1
    # and the reverse: expecting the base article must not accept the -bis
    assert correct_rank([make_hit("codice-penale", "62-bis")], "codice-penale", "62") is None


def test_empty_hits_is_a_miss() -> None:
    assert correct_rank([], "codice-civile", "2051") is None


# --- evaluate: metrics --------------------------------------------------------


def fixed_search(answers: dict[str, list[SearchHit]]):
    """Search fn returning a canned hit list per query text."""

    def search(query: str) -> list[SearchHit]:
        return answers[query]

    return search


def test_metrics_recall_and_mrr() -> None:
    queries = [
        make_query("q1", kind="explicit"),  # hit at rank 1
        make_query("q2", kind="natural"),  # hit at rank 7 -> @10 yes, @5 no
        make_query("q3", kind="lay"),  # miss
    ]
    target = make_hit("codice-civile", "2051")
    noise = make_hit("codice-penale", "999")
    answers = {
        "query q1": [target] + [noise] * 9,
        "query q2": [noise] * 6 + [target] + [noise] * 3,
        "query q3": [noise] * 10,
    }
    report = evaluate(
        queries,
        fixed_search(answers),
        collection="norme_test",
        embedder_name="fake",
        k=10,
        vigenza="vigente",
    )
    assert report.queries == 3
    assert report.recall_at_5 == 1 / 3
    assert report.recall_at_10 == 2 / 3
    assert report.mrr == (1.0 + 1.0 / 7 + 0.0) / 3
    ranks = {r.id: r.rank for r in report.results}
    assert ranks == {"q1": 1, "q2": 7, "q3": None}


def test_per_kind_breakdown() -> None:
    queries = [
        make_query("q1", kind="explicit"),
        make_query("q2", kind="explicit"),
        make_query("q3", kind="trap"),
    ]
    target = make_hit("codice-civile", "2051")
    noise = make_hit("codice-penale", "999")
    answers = {
        "query q1": [target],
        "query q2": [noise],
        "query q3": [target],
    }
    report = evaluate(
        queries,
        fixed_search(answers),
        collection="norme_test",
        embedder_name="fake",
        k=10,
        vigenza="vigente",
    )
    assert set(report.by_kind) == {"explicit", "trap"}  # no lay/natural queries
    explicit = report.by_kind["explicit"]
    assert explicit.queries == 2
    assert explicit.recall_at_10 == 0.5
    assert explicit.mrr == 0.5
    trap = report.by_kind["trap"]
    assert (trap.queries, trap.recall_at_10, trap.mrr) == (1, 1.0, 1.0)


def test_format_report_lists_misses_with_top3() -> None:
    queries = [make_query("q1", kind="lay", act_ref="codice-civile", article="896")]
    hits = [
        make_hit("codice-civile", "894", header="Codice civile\nArt. 894"),
        make_hit("codice-civile", "895", header="Codice civile\nArt. 895"),
        make_hit("codice-penale", "1", header="Codice penale\nArt. 1"),
        make_hit("codice-penale", "2", header="Codice penale\nArt. 2"),
    ]
    report = evaluate(
        queries,
        fixed_search({"query q1": hits}),
        collection="norme_test",
        embedder_name="fake",
        k=10,
        vigenza="vigente",
    )
    text = format_report(report)
    assert "MISSES (1):" in text
    assert "q1 [lay]" in text
    assert "expected: codice-civile art. 896" in text
    assert "1. codice-civile art. 894 — Codice civile" in text
    assert "3. codice-penale art. 1" in text
    assert "art. 2 —" not in text  # only top-3 shown
    # header is truncated to its first line
    assert "Art. 894" not in text.split("1. ")[1].splitlines()[0]


def test_format_report_no_misses() -> None:
    queries = [make_query("q1")]
    report = evaluate(
        queries,
        fixed_search({"query q1": [make_hit("codice-civile", "2051")]}),
        collection="norme_test",
        embedder_name="fake",
        k=10,
        vigenza="vigente",
    )
    assert "No misses." in format_report(report)
    assert "100.0%" in format_report(report)


def test_write_json_report(tmp_path: Path) -> None:
    queries = [make_query("q1")]
    report = evaluate(
        queries,
        fixed_search({"query q1": [make_hit("codice-civile", "2051")]}),
        collection="norme_test",
        embedder_name="fake",
        k=10,
        vigenza="vigente",
    )
    path = write_json_report(report, tmp_path / "results")
    assert path.parent == tmp_path / "results"
    assert path.name.startswith("norme_test-")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["recall_at_10"] == 1.0
    assert data["embedder"] == "fake"
    assert data["results"][0]["rank"] == 1
    assert data["results"][0]["top"][0]["act_ref"] == "codice-civile"


# --- queries.yaml loading -----------------------------------------------------


def test_load_real_queries_yaml() -> None:
    queries = load_queries(QUERIES_PATH)
    assert len(queries) == 30
    kinds = {q.kind for q in queries}
    assert kinds == {"explicit", "natural", "lay", "trap"}
    q15 = next(q for q in queries if q.id == "q15")
    assert q15.expected_article == "62-bis"  # suffix articles survive YAML loading
    assert all(isinstance(q.expected_article, str) for q in queries)
