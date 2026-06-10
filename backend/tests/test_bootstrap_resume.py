"""Tests for legger.ingestion.bootstrap (Task D2).

Resume/dedup semantics run against the REAL local Postgres (``-m db``, like
test_db.py) with an in-memory Qdrant, a fake embedder and a fake sparse
model: the checkpointing is the heart of D2 and must be exercised against
real upsert/conflict behaviour. The corpus is synthetic (tmp_path): test
collection folders sort deterministically and act numbers use the 99xxx/2099
range so nothing can collide with real ingestion rows; every row written is
deleted by the ``engine`` fixture.

Dry-run and pure-unit tests (blob sha, file discovery) need no services.
"""

import os
import signal
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest
from qdrant_client import QdrantClient
from sqlalchemy import Engine, select

import legger.ingestion.bootstrap as bootstrap_module
from legger.db import acts, get_engine, ingestion_progress, ingestion_runs
from legger.ingestion.bootstrap import (
    BootstrapReport,
    _FileTask,
    _LotFlusher,
    bootstrap,
    corpus_head_sha,
    discover_files,
    file_blob_sha,
)
from legger.settings import Settings

FIXTURES_CORPUS = Path(__file__).parent / "fixtures" / "corpus"

# Test collection folders. Sorted order drives "first file wins" dedup:
# "AA D2 Leggi" < "Atti normativi abrogati (in originale)" < "ZZ D2 Leggi".
COLL_FIRST = "AA D2 Leggi"
COLL_ABROGATI = "Atti normativi abrogati (in originale)"  # vigenza: abrogato
COLL_LAST = "ZZ D2 Leggi"


def synthetic_act(number: int, *, articles: int = 2) -> str:
    """A minimal parseable act whose header yields ``legge-<number>-2099``."""
    parts = [
        f"LEGGE 1 gennaio 2099, n. {number}",
        "=" * 40,
        "",
        f"Atto sintetico di prova D2 numero {number}.",
        "-" * 40,
        "",
    ]
    for art in range(1, articles + 1):
        parts += [
            f"Art. {art}",
            "------",
            "",
            f"1. Primo comma dell'articolo {art} dell'atto {number}.",
            "",
            f"2. Secondo comma dell'articolo {art} dell'atto {number}.",
            "",
        ]
    return "\n".join(parts)


def write_act(corpus: Path, collection: str, filename: str, content: str) -> Path:
    folder = corpus / collection
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / filename
    path.write_text(content, encoding="utf-8")
    return path


class FakeEmbedder:
    """Deterministic embedder recording every embed call."""

    name = "fake"
    dim = 8

    def __init__(self) -> None:
        self.calls: list[int] = []  # texts per embed_documents call

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(len(texts))
        return [[0.1] * self.dim for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        return [0.1] * self.dim


class FailingEmbedder(FakeEmbedder):
    """Succeeds for ``ok_calls`` embed calls, then always raises."""

    def __init__(self, ok_calls: int) -> None:
        super().__init__()
        self.ok_calls = ok_calls

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if len(self.calls) >= self.ok_calls:
            self.calls.append(len(texts))
            raise RuntimeError("synthetic embed failure")
        return super().embed_documents(texts)


class SigintEmbedder(FakeEmbedder):
    """Sends SIGINT to the current process during the first embed call."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        first = not self.calls
        vectors = super().embed_documents(texts)
        if first:
            os.kill(os.getpid(), signal.SIGINT)
        return vectors


class _FakeSparseVec:
    def __init__(self) -> None:
        self.indices = np.array([1], dtype=np.int64)
        self.values = np.array([0.5], dtype=np.float32)


class FakeSparseModel:
    """Stands in for fastembed BM25 (no model download in tests)."""

    def embed(self, texts: list[str]) -> list[_FakeSparseVec]:
        return [_FakeSparseVec() for _ in texts]


# ---------------------------------------------------------------------------
# Pure unit tests (no services)
# ---------------------------------------------------------------------------


def test_file_blob_sha_matches_git_hash_object() -> None:
    # `printf 'hello\n' | git hash-object --stdin`
    assert file_blob_sha(b"hello\n") == "ce013625030ba8dba906f756967f9e9ca394464a"


def test_corpus_head_sha_outside_git_repo(tmp_path: Path) -> None:
    assert corpus_head_sha(tmp_path) is None


def test_discover_files_sorted_and_validated(tmp_path: Path) -> None:
    write_act(tmp_path, "B Coll", "b.md", "x")
    write_act(tmp_path, "B Coll", "a.md", "x")
    write_act(tmp_path, "A Coll", "z.md", "x")
    (tmp_path / ".git").mkdir()  # hidden dirs are excluded
    (tmp_path / "README.md").write_text("not a collection")

    rel = [p.relative_to(tmp_path).as_posix() for p in discover_files(tmp_path)]
    assert rel == ["A Coll/z.md", "B Coll/a.md", "B Coll/b.md"]

    only_b = [p.relative_to(tmp_path).as_posix() for p in discover_files(tmp_path, ["B Coll"])]
    assert only_b == ["B Coll/a.md", "B Coll/b.md"]

    with pytest.raises(FileNotFoundError):
        discover_files(tmp_path, ["Missing Coll"])


def test_lot_flusher_releases_files_only_when_fully_durable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """3-chunk files behind a lot_size-4 buffer: a file's callback fires only
    when ALL its chunks are durable, and a dup is never released before its
    owner."""
    flushed_lots: list[int] = []

    def fake_index_chunks(client, name, lot, embedder, sparse_model, *, lot_size, resume):
        flushed_lots.append(len(lot))
        return len(lot), 0

    monkeypatch.setattr(bootstrap_module, "index_chunks", fake_index_chunks)
    released: list[str] = []
    flusher = _LotFlusher(
        None, "c", None, None, 4, lambda tasks: released.extend(t.file_path for t in tasks)
    )

    def task(name: str, kind: str = "index") -> _FileTask:
        return _FileTask(name, "sha", f"ref-{name}", kind)

    flusher.add(task("a"), [object()] * 3)  # type: ignore[arg-type]
    assert flushed_lots == []  # 3 < 4: nothing flushed
    assert released == []  # a's chunks are not durable yet
    flusher.add(task("a-dup", kind="dup"), [])
    assert released == []  # 0-chunk dup queued behind its owner, NOT released first
    flusher.add(task("b"), [object()] * 3)  # type: ignore[arg-type]
    assert flushed_lots == [4]  # 6 buffered -> one full lot (a:3 + b:1)
    assert released == ["a", "a-dup"]  # owner first, then its dup; b still has 2 pending
    flusher.finish()
    assert flushed_lots == [4, 2]  # tail lot
    assert released == ["a", "a-dup", "b"]
    assert flusher.chunks_indexed == 6


def test_dry_run_on_fixtures_corpus_needs_no_services() -> None:
    settings = Settings(corpus_path=FIXTURES_CORPUS)
    report = bootstrap(dry_run=True, settings=settings)

    assert report.status == "dry-run"
    assert report.run_id is None
    assert report.files_total == 9  # the committed fixture corpus
    assert report.files_processed + report.files_dedup_skipped + len(report.errors) == 9
    assert report.est_chunks > 0
    assert report.total_chars > 0
    assert report.est_tokens == round(report.total_chars / 2.26)
    assert set(report.per_collection) == {
        "Atti di attuazione Regolamenti UE",
        "Atti normativi abrogati (in originale)",
        "Codici",
        "DL decaduti",
        "Leggi finanziarie e di bilancio",
        "Regi decreti",
    }


def crashing_chunk_act_for(filename: str):
    """A chunk_act wrapper raising only for files whose path ends in ``filename``."""
    real_chunk_act = bootstrap_module.chunk_act

    def crashing_chunk_act(act, ref, **kwargs):
        if kwargs.get("file_path", "").endswith(filename):
            raise ValueError("synthetic chunker crash")
        return real_chunk_act(act, ref, **kwargs)

    return crashing_chunk_act


def test_dry_run_chunker_crash_is_per_file_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_act(tmp_path, COLL_FIRST, "atto-99001.md", synthetic_act(99001))
    write_act(tmp_path, COLL_FIRST, "atto-99002.md", synthetic_act(99002))
    monkeypatch.setattr(bootstrap_module, "chunk_act", crashing_chunk_act_for("atto-99001.md"))

    report = bootstrap(dry_run=True, settings=Settings(corpus_path=tmp_path))

    assert report.status == "dry-run"
    assert report.files_processed == 1  # the healthy file is still counted
    assert len(report.errors) == 1
    assert report.errors[0]["file_path"] == f"{COLL_FIRST}/atto-99001.md"
    assert "ValueError" in report.errors[0]["error"]


# ---------------------------------------------------------------------------
# Resume/dedup semantics against the real Postgres (-m db)
# ---------------------------------------------------------------------------


@pytest.fixture
def engine() -> Iterator[Engine]:
    eng = get_engine()
    created_runs: list[int] = []
    eng.created_run_ids = created_runs  # type: ignore[attr-defined]
    yield eng
    with eng.begin() as conn:
        conn.execute(acts.delete().where(acts.c.year == 2099))
        for prefix in (COLL_FIRST, COLL_LAST):
            conn.execute(
                ingestion_progress.delete().where(
                    ingestion_progress.c.file_path.like(f"{prefix}/%")
                )
            )
        conn.execute(
            ingestion_progress.delete().where(
                ingestion_progress.c.file_path.like(f"{COLL_ABROGATI}/d2-test-%")
            )
        )
        if created_runs:
            conn.execute(ingestion_runs.delete().where(ingestion_runs.c.id.in_(created_runs)))
    eng.dispose()


def run_bootstrap(
    corpus: Path,
    engine: Engine,
    qdrant: QdrantClient,
    embedder: FakeEmbedder | None = None,
    *,
    lot_size: int = 256,
) -> BootstrapReport:
    report = bootstrap(
        settings=Settings(corpus_path=corpus),
        engine=engine,
        qdrant_client=qdrant,
        embedder=embedder or FakeEmbedder(),
        sparse_model=FakeSparseModel(),
        qdrant_collection="norme_d2_test",
        lot_size=lot_size,
    )
    if report.run_id is not None:
        engine.created_run_ids.append(report.run_id)  # type: ignore[attr-defined]
    return report


@pytest.fixture
def qdrant() -> QdrantClient:
    return QdrantClient(":memory:")


@pytest.mark.db
def test_bootstrap_indexes_records_and_closes_run(
    tmp_path: Path, engine: Engine, qdrant: QdrantClient
) -> None:
    write_act(tmp_path, COLL_FIRST, "atto-99001.md", synthetic_act(99001))
    write_act(tmp_path, COLL_FIRST, "atto-99002.md", synthetic_act(99002, articles=3))

    report = run_bootstrap(tmp_path, engine, qdrant)

    assert report.status == "completed"
    assert report.files_total == 2
    assert report.files_processed == 2
    assert report.files_skipped == 0
    assert report.chunks_indexed == 5  # 2 + 3 articles, one chunk each
    assert qdrant.count("norme_d2_test").count == 5

    with engine.connect() as conn:
        act = conn.execute(select(acts).where(acts.c.act_ref == "legge-99001-2099")).one()
        assert act.act_type == "legge"
        assert act.vigenza == "vigente"
        assert act.collection == COLL_FIRST
        assert act.file_path == f"{COLL_FIRST}/atto-99001.md"
        assert act.year == 2099
        assert act.source == "header"

        progress = conn.execute(
            select(ingestion_progress).where(
                ingestion_progress.c.file_path == f"{COLL_FIRST}/atto-99001.md"
            )
        ).one()
        expected_sha = file_blob_sha((tmp_path / COLL_FIRST / "atto-99001.md").read_bytes())
        assert progress.commit_sha == expected_sha
        assert progress.act_ref == "legge-99001-2099"

        run = conn.execute(select(ingestion_runs).where(ingestion_runs.c.id == report.run_id)).one()
        assert run.kind == "bootstrap"
        assert run.status == "completed"
        assert run.files_processed == 2
        assert run.files_skipped == 0
        assert run.errors == []
        assert run.started_at is not None and run.finished_at is not None


@pytest.mark.db
def test_rerun_skips_completed_files_without_embedding(
    tmp_path: Path, engine: Engine, qdrant: QdrantClient
) -> None:
    write_act(tmp_path, COLL_FIRST, "atto-99001.md", synthetic_act(99001))
    write_act(tmp_path, COLL_FIRST, "atto-99002.md", synthetic_act(99002))
    run_bootstrap(tmp_path, engine, qdrant)

    embedder = FakeEmbedder()
    report = run_bootstrap(tmp_path, engine, qdrant, embedder)

    assert report.status == "completed"
    assert report.files_processed == 0
    assert report.files_resume_skipped == 2
    assert report.chunks_indexed == 0
    assert embedder.calls == []  # file-level resume: not even parsed/embedded


@pytest.mark.db
def test_modified_file_is_reprocessed(tmp_path: Path, engine: Engine, qdrant: QdrantClient) -> None:
    path = write_act(tmp_path, COLL_FIRST, "atto-99001.md", synthetic_act(99001))
    write_act(tmp_path, COLL_FIRST, "atto-99002.md", synthetic_act(99002))
    run_bootstrap(tmp_path, engine, qdrant)

    path.write_text(synthetic_act(99001, articles=4), encoding="utf-8")
    report = run_bootstrap(tmp_path, engine, qdrant)

    assert report.files_processed == 1  # only the modified file
    assert report.files_resume_skipped == 1
    assert report.chunks_indexed == 4  # re-chunked at its new shape

    with engine.connect() as conn:
        progress = conn.execute(
            select(ingestion_progress.c.commit_sha).where(
                ingestion_progress.c.file_path == f"{COLL_FIRST}/atto-99001.md"
            )
        ).scalar_one()
    assert progress == file_blob_sha(path.read_bytes())  # sha refreshed


@pytest.mark.db
def test_interrupted_run_resumes_where_it_stopped(
    tmp_path: Path, engine: Engine, qdrant: QdrantClient
) -> None:
    for number in range(99001, 99007):  # 6 files x 2 chunks each
        write_act(tmp_path, COLL_FIRST, f"atto-{number}.md", synthetic_act(number))

    # lot_size=4: lot 1 (files 1-2) succeeds, lot 2 fails twice -> run aborts.
    failing = FailingEmbedder(ok_calls=1)
    report1 = run_bootstrap(tmp_path, engine, qdrant, failing, lot_size=4)
    assert report1.status == "failed"
    assert report1.note is not None and "fatal" in report1.note
    assert report1.files_processed == 2  # only the durable (flushed) files
    with engine.connect() as conn:
        run = conn.execute(
            select(ingestion_runs).where(ingestion_runs.c.id == report1.run_id)
        ).one()
        assert run.status == "failed"
        assert run.files_processed == 2
        done = (
            conn.execute(
                select(ingestion_progress.c.file_path).where(
                    ingestion_progress.c.file_path.like(f"{COLL_FIRST}/%")
                )
            )
            .scalars()
            .all()
        )
    assert sorted(done) == [f"{COLL_FIRST}/atto-9900{i}.md" for i in (1, 2)]

    # Plain re-run with a healthy embedder resumes after the durable files.
    report2 = run_bootstrap(tmp_path, engine, qdrant, lot_size=4)
    assert report2.status == "completed"
    assert report2.files_resume_skipped == 2
    assert report2.files_processed == 4
    assert qdrant.count("norme_d2_test").count == 12  # complete index


@pytest.mark.db
def test_dedup_keeps_first_file_and_applies_vigenza_exception(
    tmp_path: Path, engine: Engine, qdrant: QdrantClient
) -> None:
    # Act 99001: vigente owner first, abrogato duplicate later -> vigenza flips.
    write_act(tmp_path, COLL_FIRST, "atto-99001.md", synthetic_act(99001))
    write_act(tmp_path, COLL_ABROGATI, "d2-test-atto-99001.md", synthetic_act(99001, articles=5))
    # Act 99002: abrogato owner first, vigente duplicate later -> stays abrogato.
    write_act(tmp_path, COLL_ABROGATI, "d2-test-atto-99002.md", synthetic_act(99002))
    write_act(tmp_path, COLL_LAST, "atto-99002.md", synthetic_act(99002))

    report = run_bootstrap(tmp_path, engine, qdrant)

    assert report.status == "completed"
    assert report.files_processed == 2  # one owner per act_ref
    assert report.files_dedup_skipped == 2
    assert report.chunks_indexed == 4  # owners only (2+2); the 5-article dup skipped

    with engine.connect() as conn:
        act1 = conn.execute(select(acts).where(acts.c.act_ref == "legge-99001-2099")).one()
        act2 = conn.execute(select(acts).where(acts.c.act_ref == "legge-99002-2099")).one()
        dup_progress = conn.execute(
            select(ingestion_progress.c.act_ref).where(
                ingestion_progress.c.file_path == f"{COLL_ABROGATI}/d2-test-atto-99001.md"
            )
        ).scalar_one()
    # First processed file owns the act row...
    assert act1.file_path == f"{COLL_FIRST}/atto-99001.md"
    assert act1.collection == COLL_FIRST
    # ...but the abrogati folder wins the vigenza (A7 exception).
    assert act1.vigenza == "abrogato"
    # Reverse order: vigente NEVER downgrades an abrogato record.
    assert act2.vigenza == "abrogato"
    assert act2.file_path == f"{COLL_ABROGATI}/d2-test-atto-99002.md"
    # The duplicate still records progress (never re-visited on resume).
    assert dup_progress == "legge-99001-2099"

    # Resume: everything (owners AND duplicates) skips.
    report2 = run_bootstrap(tmp_path, engine, qdrant)
    assert report2.files_resume_skipped == 4
    assert report2.files_processed == 0
    assert report2.files_dedup_skipped == 0


@pytest.mark.db
def test_error_file_is_recorded_and_run_continues(
    tmp_path: Path, engine: Engine, qdrant: QdrantClient
) -> None:
    write_act(tmp_path, COLL_FIRST, "atto-99001.md", synthetic_act(99001))
    bad = write_act(tmp_path, COLL_FIRST, "atto-99002-illeggibile.md", synthetic_act(99002))
    write_act(tmp_path, COLL_FIRST, "atto-99003.md", synthetic_act(99003))
    bad.chmod(0o000)
    try:
        report = run_bootstrap(tmp_path, engine, qdrant)
    finally:
        bad.chmod(0o644)

    assert report.status == "completed"  # one bad file never aborts the run
    assert report.files_processed == 2
    assert len(report.errors) == 1
    assert report.errors[0]["file_path"] == f"{COLL_FIRST}/atto-99002-illeggibile.md"
    assert "PermissionError" in report.errors[0]["error"]

    with engine.connect() as conn:
        run = conn.execute(select(ingestion_runs).where(ingestion_runs.c.id == report.run_id)).one()
        bad_progress = conn.execute(
            select(ingestion_progress).where(
                ingestion_progress.c.file_path == f"{COLL_FIRST}/atto-99002-illeggibile.md"
            )
        ).all()
    assert run.status == "completed"
    assert run.errors == report.errors  # persisted in the run's JSONB
    assert bad_progress == []  # no checkpoint: retried on the next run


@pytest.mark.db
def test_chunker_crash_is_per_file_error_not_fatal(
    tmp_path: Path, engine: Engine, qdrant: QdrantClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_act(tmp_path, COLL_FIRST, "atto-99001.md", synthetic_act(99001))
    write_act(tmp_path, COLL_FIRST, "atto-99002.md", synthetic_act(99002))
    write_act(tmp_path, COLL_FIRST, "atto-99003.md", synthetic_act(99003))
    real_chunk_act = bootstrap_module.chunk_act
    monkeypatch.setattr(bootstrap_module, "chunk_act", crashing_chunk_act_for("atto-99002.md"))

    report = run_bootstrap(tmp_path, engine, qdrant)

    assert report.status == "completed"  # NOT a fatal abort
    assert report.files_processed == 2  # the other files were still indexed
    assert len(report.errors) == 1
    assert report.errors[0]["file_path"] == f"{COLL_FIRST}/atto-99002.md"
    assert "ValueError" in report.errors[0]["error"]
    with engine.connect() as conn:
        run = conn.execute(select(ingestion_runs).where(ingestion_runs.c.id == report.run_id)).one()
        crashed_progress = conn.execute(
            select(ingestion_progress).where(
                ingestion_progress.c.file_path == f"{COLL_FIRST}/atto-99002.md"
            )
        ).all()
    assert run.status == "completed"
    assert run.errors == report.errors
    assert crashed_progress == []  # no checkpoint: retried on the next run

    # Re-run with a healthy chunker: no crash loop, only the failed file is paid.
    monkeypatch.setattr(bootstrap_module, "chunk_act", real_chunk_act)
    report2 = run_bootstrap(tmp_path, engine, qdrant)
    assert report2.status == "completed"
    assert report2.files_processed == 1
    assert report2.files_resume_skipped == 2


class KillingEmbedder(FakeEmbedder):
    """Raises KeyboardInterrupt mid-embed: simulates a hard kill (2nd SIGINT)."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise KeyboardInterrupt


@pytest.mark.db
def test_hard_kill_closes_run_as_failed_and_reraises(
    tmp_path: Path, engine: Engine, qdrant: QdrantClient
) -> None:
    write_act(tmp_path, COLL_FIRST, "atto-99001.md", synthetic_act(99001))
    write_act(tmp_path, COLL_FIRST, "atto-99002.md", synthetic_act(99002))

    with engine.connect() as conn:
        before = set(conn.execute(select(ingestion_runs.c.id)).scalars())
    with pytest.raises(KeyboardInterrupt):  # the kill is re-raised, not swallowed
        bootstrap(
            settings=Settings(corpus_path=tmp_path),
            engine=engine,
            qdrant_client=qdrant,
            embedder=KillingEmbedder(),
            sparse_model=FakeSparseModel(),
            qdrant_collection="norme_d2_test",
            lot_size=4,  # 2 files x 2 chunks: the kill lands on the first flush
        )
    with engine.connect() as conn:
        new_ids = sorted(set(conn.execute(select(ingestion_runs.c.id)).scalars()) - before)
    engine.created_run_ids.extend(new_ids)  # type: ignore[attr-defined]
    assert len(new_ids) == 1

    with engine.connect() as conn:
        run = conn.execute(select(ingestion_runs).where(ingestion_runs.c.id == new_ids[0])).one()
    assert run.status == "failed"  # NEVER 'completed' on a hard kill
    assert run.finished_at is not None
    assert any("KeyboardInterrupt" in (e["error"] or "") for e in run.errors)


@pytest.mark.db
def test_sigint_closes_run_as_failed_and_resume_completes(
    tmp_path: Path, engine: Engine, qdrant: QdrantClient
) -> None:
    for number in range(99001, 99007):  # 6 files x 2 chunks each
        write_act(tmp_path, COLL_FIRST, f"atto-{number}.md", synthetic_act(number))

    # SIGINT lands during the first lot's embed call; the loop stops before
    # the next file and the buffer is drained so parsed files stay durable.
    report1 = run_bootstrap(tmp_path, engine, qdrant, SigintEmbedder(), lot_size=4)
    assert report1.status == "failed"
    assert report1.note is not None and "SIGINT" in report1.note
    assert 0 < report1.files_processed < 6

    with engine.connect() as conn:
        run = conn.execute(
            select(ingestion_runs).where(ingestion_runs.c.id == report1.run_id)
        ).one()
    assert run.status == "failed"
    assert any(e["error"] and "SIGINT" in e["error"] for e in run.errors)

    report2 = run_bootstrap(tmp_path, engine, qdrant, lot_size=4)
    assert report2.status == "completed"
    assert report2.files_resume_skipped == report1.files_processed
    assert report2.files_processed == 6 - report1.files_processed
    assert qdrant.count("norme_d2_test").count == 12
