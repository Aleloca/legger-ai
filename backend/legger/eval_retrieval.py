"""Retrieval evaluation harness over ``backend/eval/queries.yaml`` (Task C4).

For each of the 30 queries the harness runs :func:`hybrid_search` and judges
HIT/MISS: a hit is any retrieved chunk whose payload ``act_ref`` AND
``article`` both equal the expected values (plain string equality — ``"2-bis"``
is NOT ``"2"``; -bis/-ter articles are first-class citizens here).

Metrics: recall@5, recall@10 and MRR (reciprocal rank of the FIRST correct
hit, 0 on a miss), overall and per query kind (explicit/natural/lay/trap).
recall@10 is THE C6 go/no-go number (gate: >= 85%).

Every run writes a machine-readable JSON report to
``backend/eval/results/{collection}-{timestamp}.json`` — committed, not
gitignored: the results are the go/no-go evidence. The human-readable output
ends with the MISS list (top-3 results each), the starting point for any
post-mortem if the numbers disappoint.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable, Sequence
from pathlib import Path

import yaml
from pydantic import BaseModel
from qdrant_client import QdrantClient

from legger.retrieval.embedders import get_embedder
from legger.retrieval.search import SearchHit, hybrid_search
from legger.settings import Settings

#: Query kinds in display order (matches queries.yaml).
KINDS = ("explicit", "natural", "lay", "trap")

QUERIES_PATH = Path(__file__).resolve().parents[1] / "eval" / "queries.yaml"
RESULTS_DIR = Path(__file__).resolve().parents[1] / "eval" / "results"

SearchFn = Callable[[str], list[SearchHit]]


class EvalQuery(BaseModel):
    """One entry of queries.yaml."""

    id: str
    query: str
    kind: str
    note: str = ""
    expected_act_ref: str
    expected_article: str


def load_queries(path: Path = QUERIES_PATH) -> list[EvalQuery]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [
        EvalQuery(
            id=entry["id"],
            query=entry["query"],
            kind=entry["kind"],
            note=entry.get("note", ""),
            expected_act_ref=entry["expected"]["act_ref"],
            expected_article=entry["expected"]["article"],
        )
        for entry in raw
    ]


class HitSummary(BaseModel):
    """Compact view of one retrieved chunk, for the report."""

    act_ref: str
    article: str
    header: str  # first header line only
    score: float


class QueryResult(BaseModel):
    """Outcome of one eval query."""

    id: str
    kind: str
    query: str
    expected_act_ref: str
    expected_article: str
    rank: int | None  # 1-based rank of the first correct hit; None = miss
    top: list[HitSummary]  # the retrieved top-k, compact


class KindMetrics(BaseModel):
    queries: int
    recall_at_5: float
    recall_at_10: float
    mrr: float


class EvalReport(BaseModel):
    """One eval run: parameters, metrics, per-query results."""

    collection: str
    embedder: str
    k: int
    vigenza: str | None
    timestamp: str
    queries: int
    recall_at_5: float
    recall_at_10: float
    mrr: float
    by_kind: dict[str, KindMetrics]
    results: list[QueryResult]


def correct_rank(
    hits: Sequence[SearchHit], expected_act_ref: str, expected_article: str
) -> int | None:
    """1-based rank of the first hit matching BOTH expected fields, else None.

    Plain string equality on the payload ``article``: ``"2-bis" != "2"``.
    """
    for rank, hit in enumerate(hits, start=1):
        if hit.act_ref == expected_act_ref and hit.article == expected_article:
            return rank
    return None


def _metrics(ranks: Sequence[int | None]) -> tuple[float, float, float]:
    """(recall@5, recall@10, MRR) over a list of first-correct ranks."""
    n = len(ranks)
    if n == 0:
        return 0.0, 0.0, 0.0
    r5 = sum(1 for r in ranks if r is not None and r <= 5) / n
    r10 = sum(1 for r in ranks if r is not None and r <= 10) / n
    mrr = sum(1.0 / r for r in ranks if r is not None) / n
    return r5, r10, mrr


def evaluate(
    queries: Sequence[EvalQuery],
    search: SearchFn,
    *,
    collection: str,
    embedder_name: str,
    k: int,
    vigenza: str | None,
) -> EvalReport:
    """Run ``search`` over every query and assemble the full report."""
    results: list[QueryResult] = []
    for q in queries:
        hits = search(q.query)
        results.append(
            QueryResult(
                id=q.id,
                kind=q.kind,
                query=q.query,
                expected_act_ref=q.expected_act_ref,
                expected_article=q.expected_article,
                rank=correct_rank(hits, q.expected_act_ref, q.expected_article),
                top=[
                    HitSummary(
                        act_ref=h.act_ref,
                        article=h.article,
                        header=h.header.splitlines()[0] if h.header else "",
                        score=h.score,
                    )
                    for h in hits
                ],
            )
        )

    r5, r10, mrr = _metrics([r.rank for r in results])
    by_kind: dict[str, KindMetrics] = {}
    for kind in KINDS:
        kind_ranks = [r.rank for r in results if r.kind == kind]
        if not kind_ranks:
            continue
        k5, k10, kmrr = _metrics(kind_ranks)
        by_kind[kind] = KindMetrics(
            queries=len(kind_ranks), recall_at_5=k5, recall_at_10=k10, mrr=kmrr
        )

    return EvalReport(
        collection=collection,
        embedder=embedder_name,
        k=k,
        vigenza=vigenza,
        timestamp=dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        queries=len(results),
        recall_at_5=r5,
        recall_at_10=r10,
        mrr=mrr,
        by_kind=by_kind,
        results=results,
    )


def format_report(report: EvalReport) -> str:
    """Human-readable summary: metrics table, per-kind breakdown, miss list."""
    lines: list[str] = []
    lines.append(
        f"Retrieval eval — collection={report.collection} embedder={report.embedder} "
        f"k={report.k} vigenza={report.vigenza} ({report.timestamp})"
    )
    lines.append("")
    header = f"{'kind':<10} {'n':>3} {'recall@5':>9} {'recall@10':>10} {'MRR':>6}"
    lines.append(header)
    lines.append("-" * len(header))
    lines.append(
        f"{'ALL':<10} {report.queries:>3} {report.recall_at_5:>9.1%} "
        f"{report.recall_at_10:>10.1%} {report.mrr:>6.3f}"
    )
    for kind, m in report.by_kind.items():
        lines.append(
            f"{kind:<10} {m.queries:>3} {m.recall_at_5:>9.1%} {m.recall_at_10:>10.1%} {m.mrr:>6.3f}"
        )

    misses = [r for r in report.results if r.rank is None]
    lines.append("")
    if not misses:
        lines.append("No misses.")
    else:
        lines.append(f"MISSES ({len(misses)}):")
        for r in misses:
            lines.append(f"  {r.id} [{r.kind}] {r.query}")
            lines.append(f"    expected: {r.expected_act_ref} art. {r.expected_article}")
            for i, hit in enumerate(r.top[:3], start=1):
                lines.append(f"    {i}. {hit.act_ref} art. {hit.article} — {hit.header}")
    return "\n".join(lines)


def write_json_report(report: EvalReport, results_dir: Path = RESULTS_DIR) -> Path:
    """Write the machine-readable report; returns the file path."""
    results_dir.mkdir(parents=True, exist_ok=True)
    stamp = report.timestamp.replace(":", "").replace("-", "").replace("T", "-").rstrip("Z")
    path = results_dir / f"{report.collection}-{stamp}.json"
    path.write_text(
        json.dumps(report.model_dump(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return path


def run_eval(
    collection: str,
    embedder_name: str,
    *,
    k: int = 10,
    vigenza: str | None = "vigente",
    settings: Settings | None = None,
    queries_path: Path = QUERIES_PATH,
    results_dir: Path = RESULTS_DIR,
) -> tuple[EvalReport, Path]:
    """Full eval run against the live Qdrant: report + JSON path."""
    settings = settings or Settings()
    embedder = get_embedder(embedder_name)
    client = QdrantClient(url=settings.qdrant_url)
    queries = load_queries(queries_path)

    def search(query: str) -> list[SearchHit]:
        return hybrid_search(
            query,
            collection=collection,
            embedder=embedder,
            client=client,
            k=k,
            vigenza=vigenza,
        )

    report = evaluate(
        queries,
        search,
        collection=collection,
        embedder_name=embedder_name,
        k=k,
        vigenza=vigenza,
    )
    path = write_json_report(report, results_dir)
    return report, path
