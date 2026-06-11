"""Tests for legger.ingestion.delta (Task D3).

The corpus is a synthetic git repo built in ``tmp_path`` (the production
corpus is never touched: ``pull=False`` everywhere except the dedicated
pull test, which pulls from a sibling tmp repo). Like test_bootstrap_resume,
the delta semantics run against the REAL local Postgres (``-m db``) with an
in-memory Qdrant and fake embedder/sparse models; test data uses the
99xxx/2099 act-number range and dedicated collection folders so nothing can
collide with production rows, and the ``engine`` fixture deletes every row
the tests create.

Range determination relies on "the run with the highest id wins": the runs
the tests create are always newer than any production run already in the
table, so reading the latest successful commit_to is deterministic even on
a shared database.
"""

import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from qdrant_client import QdrantClient, models
from sqlalchemy import Engine, func, select

import legger.ingestion.delta as delta_module
from legger.db import acts, get_engine, ingestion_progress, ingestion_runs
from legger.ingestion.bootstrap import bootstrap, file_blob_sha
from legger.ingestion.delta import DeltaRefusedError, delta, diff_changes
from legger.settings import Settings
from tests.test_bootstrap_resume import (
    FakeEmbedder,
    FakeSparseModel,
    crashing_chunk_act_for,
    synthetic_act,
    write_act,
)

# Test collection folders (distinct from the D2 ones, see the engine fixture).
COLL = "AA D3 Leggi"
COLL_LAST = "ZZ D3 Leggi"
COLL_ABROGATI = "Atti normativi abrogati (in originale)"  # vigenza: abrogato
QCOLL = "norme_d3_test"


# ---------------------------------------------------------------------------
# Git + run helpers
# ---------------------------------------------------------------------------


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    )
    return proc.stdout


def commit_all(repo: Path, message: str = "update") -> str:
    """Stage everything, commit, return the new HEAD sha."""
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--allow-empty", "-m", message)
    return git(repo, "rev-parse", "HEAD").strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / "corpus"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "d3@test.local")
    git(repo, "config", "user.name", "D3 Test")
    return repo


@pytest.fixture
def engine() -> Iterator[Engine]:
    eng = get_engine()
    created_runs: list[int] = []
    eng.created_run_ids = created_runs  # type: ignore[attr-defined]
    yield eng
    with eng.begin() as conn:
        conn.execute(acts.delete().where(acts.c.year == 2099))
        for prefix in (COLL, COLL_LAST):
            conn.execute(
                ingestion_progress.delete().where(
                    ingestion_progress.c.file_path.like(f"{prefix}/%")
                )
            )
        conn.execute(
            ingestion_progress.delete().where(
                ingestion_progress.c.file_path.like(f"{COLL_ABROGATI}/d3-test-%")
            )
        )
        if created_runs:
            conn.execute(ingestion_runs.delete().where(ingestion_runs.c.id.in_(created_runs)))
    eng.dispose()


@pytest.fixture
def qdrant() -> QdrantClient:
    return QdrantClient(":memory:")


def run_bootstrap(repo: Path, engine: Engine, qdrant: QdrantClient):
    report = bootstrap(
        settings=Settings(corpus_path=repo),
        engine=engine,
        qdrant_client=qdrant,
        embedder=FakeEmbedder(),
        sparse_model=FakeSparseModel(),
        qdrant_collection=QCOLL,
        lot_size=64,
    )
    assert report.status == "completed"
    engine.created_run_ids.append(report.run_id)  # type: ignore[attr-defined]
    return report


def run_delta(
    repo: Path,
    engine: Engine,
    qdrant: QdrantClient,
    embedder: FakeEmbedder | None = None,
    *,
    pull: bool = False,
):
    report = delta(
        settings=Settings(corpus_path=repo),
        engine=engine,
        qdrant_client=qdrant,
        embedder=embedder or FakeEmbedder(),
        sparse_model=FakeSparseModel(),
        qdrant_collection=QCOLL,
        pull=pull,
        lot_size=64,
    )
    if report.run_id is not None:
        engine.created_run_ids.append(report.run_id)  # type: ignore[attr-defined]
    return report


def act_points(qdrant: QdrantClient, act_ref: str) -> list:
    flt = models.Filter(
        must=[models.FieldCondition(key="act_ref", match=models.MatchValue(value=act_ref))]
    )
    points, _ = qdrant.scroll(
        QCOLL, scroll_filter=flt, limit=1000, with_payload=True, with_vectors=False
    )
    return points


# ---------------------------------------------------------------------------
# Pure unit tests (git only, no services)
# ---------------------------------------------------------------------------


def test_diff_changes_classifies_statuses_and_renames(repo: Path) -> None:
    write_act(repo, COLL, "a.md", synthetic_act(99001))
    # b.md gets deliberately distinctive content so git's rename detection
    # cannot pair its deletion with the addition of nuovo.md.
    write_act(repo, COLL, "b.md", "LEGGE 1 gennaio 2099, n. 99002\n" + "=" * 40 + "\n\nBreve.\n")
    write_act(repo, COLL, "c.md", synthetic_act(99003))
    write_act(repo, COLL, "d.md", synthetic_act(99004, articles=6))
    (repo / "README.md").write_text("root file: never a corpus act")
    c1 = commit_all(repo)

    assert diff_changes(repo, c1, c1) == []  # same commit: empty range

    write_act(repo, COLL, "nuovo.md", synthetic_act(99005))  # A
    write_act(repo, COLL, "a.md", synthetic_act(99001, articles=3))  # M
    (repo / COLL / "b.md").unlink()  # D
    git(repo, "mv", f"{COLL}/c.md", f"{COLL}/c-rinominato.md")  # R100 (pure move)
    # R<100: rename + content edit in the same range -> delete+add pair.
    (repo / COLL / "d.md").rename(repo / COLL / "d-mod.md")
    with (repo / COLL / "d-mod.md").open("a", encoding="utf-8") as fh:
        fh.write("\nArt. 7\n------\n\n1. Comma aggiunto dopo la rinomina.\n")
    (repo / "README.md").write_text("changed, still ignored")  # non-corpus .md
    (repo / COLL / "note.txt").write_text("non-md, ignored")
    c2 = commit_all(repo)

    changes = diff_changes(repo, c1, c2)
    by_status: dict[str, list[tuple[str | None, str]]] = {}
    for change in changes:
        by_status.setdefault(change.status, []).append((change.old_path, change.path))
    assert sorted(by_status["upsert"]) == [
        (None, f"{COLL}/a.md"),
        (None, f"{COLL}/d-mod.md"),
        (None, f"{COLL}/nuovo.md"),
    ]
    assert sorted(by_status["delete"]) == [(None, f"{COLL}/b.md"), (None, f"{COLL}/d.md")]
    assert by_status["move"] == [(f"{COLL}/c.md", f"{COLL}/c-rinominato.md")]


def test_delta_refuses_without_previous_successful_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(delta_module, "_last_successful_commit", lambda engine: None)
    with pytest.raises(DeltaRefusedError, match="bootstrap"):
        delta(settings=Settings(corpus_path=tmp_path), engine=object(), pull=False)


# ---------------------------------------------------------------------------
# Delta semantics against the real Postgres (-m db) + in-memory Qdrant
# ---------------------------------------------------------------------------


@pytest.mark.db
def test_added_file_is_indexed_and_delta_run_recorded(
    repo: Path, engine: Engine, qdrant: QdrantClient
) -> None:
    write_act(repo, COLL, "atto-99001.md", synthetic_act(99001))
    commit_all(repo)
    boot = run_bootstrap(repo, engine, qdrant)

    new_path = write_act(repo, COLL, "atto-99002.md", synthetic_act(99002, articles=3))
    c2 = commit_all(repo)
    report = run_delta(repo, engine, qdrant)

    assert report.status == "completed"
    assert report.commit_from == boot.commit_to
    assert report.commit_to == c2
    assert report.files_indexed == 1
    assert report.chunks_indexed == 3
    assert qdrant.count(QCOLL).count == 2 + 3

    with engine.connect() as conn:
        act = conn.execute(select(acts).where(acts.c.act_ref == "legge-99002-2099")).one()
        assert act.vigenza == "vigente"
        assert act.file_path == f"{COLL}/atto-99002.md"
        assert act.last_commit_sha == c2
        progress = conn.execute(
            select(ingestion_progress).where(
                ingestion_progress.c.file_path == f"{COLL}/atto-99002.md"
            )
        ).one()
        assert progress.commit_sha == file_blob_sha(new_path.read_bytes())
        run = conn.execute(select(ingestion_runs).where(ingestion_runs.c.id == report.run_id)).one()
        assert run.kind == "delta"
        assert run.status == "completed"
        assert run.commit_from == boot.commit_to
        assert run.commit_to == c2
        assert run.files_processed == 1
        assert run.errors == []


@pytest.mark.db
def test_modified_file_is_reindexed_and_stale_points_deleted(
    repo: Path, engine: Engine, qdrant: QdrantClient
) -> None:
    path = write_act(repo, COLL, "atto-99001.md", synthetic_act(99001, articles=3))
    commit_all(repo)
    run_bootstrap(repo, engine, qdrant)
    assert qdrant.count(QCOLL).count == 3

    # Smaller current text: art. 3 disappears. Arts 1-2 keep the SAME chunk
    # ids (ids are content-independent) with different text, so the delta
    # must re-embed them rather than lot-resume-skip them.
    smaller = synthetic_act(99001, articles=2).replace("Primo comma", "Comma riscritto")
    path.write_text(smaller, encoding="utf-8")
    commit_all(repo)
    embedder = FakeEmbedder()
    report = run_delta(repo, engine, qdrant, embedder)

    assert report.files_indexed == 1
    assert report.chunks_indexed == 2
    assert report.stale_points_deleted == 1  # the art-3 chunk of THIS act only
    assert embedder.calls == [2]  # modified text re-embedded despite stable ids
    assert qdrant.count(QCOLL).count == 2
    points = act_points(qdrant, "legge-99001-2099")
    assert len(points) == 2
    assert all("Comma riscritto" in p.payload["text"] for p in points)
    with engine.connect() as conn:
        sha = conn.execute(
            select(ingestion_progress.c.commit_sha).where(
                ingestion_progress.c.file_path == f"{COLL}/atto-99001.md"
            )
        ).scalar_one()
    assert sha == file_blob_sha(path.read_bytes())


@pytest.mark.db
def test_pure_rename_swaps_progress_and_payload_without_reembedding(
    repo: Path, engine: Engine, qdrant: QdrantClient
) -> None:
    write_act(repo, COLL, "atto-99001.md", synthetic_act(99001))
    commit_all(repo)
    run_bootstrap(repo, engine, qdrant)

    git(repo, "mv", f"{COLL}/atto-99001.md", f"{COLL}/atto-rinominato-99001.md")
    commit_all(repo)
    embedder = FakeEmbedder()
    report = run_delta(repo, engine, qdrant, embedder)

    assert report.files_moved == 1
    assert report.files_indexed == 0
    assert embedder.calls == []  # pure move: nothing re-embedded
    assert qdrant.count(QCOLL).count == 2  # same deterministic points, no duplicates
    new_path = f"{COLL}/atto-rinominato-99001.md"
    with engine.connect() as conn:
        act = conn.execute(select(acts).where(acts.c.act_ref == "legge-99001-2099")).one()
        assert act.file_path == new_path  # act_ref stable, ownership follows the file
        assert act.vigenza == "vigente"
        old_rows = conn.execute(
            select(ingestion_progress).where(
                ingestion_progress.c.file_path == f"{COLL}/atto-99001.md"
            )
        ).all()
        assert old_rows == []  # old progress row gone
        progress = conn.execute(
            select(ingestion_progress).where(ingestion_progress.c.file_path == new_path)
        ).one()
        assert progress.act_ref == "legge-99001-2099"
    for point in act_points(qdrant, "legge-99001-2099"):
        assert point.payload["file_path"] == new_path


@pytest.mark.db
def test_rename_into_abrogati_flips_vigenza_and_keeps_points(
    repo: Path, engine: Engine, qdrant: QdrantClient
) -> None:
    write_act(repo, COLL, "atto-99001.md", synthetic_act(99001))
    commit_all(repo)
    run_bootstrap(repo, engine, qdrant)

    (repo / COLL_ABROGATI).mkdir()
    git(repo, "mv", f"{COLL}/atto-99001.md", f"{COLL_ABROGATI}/d3-test-atto-99001.md")
    commit_all(repo)
    report = run_delta(repo, engine, qdrant)

    assert report.files_moved == 1
    assert report.vigenza_flips == 1
    assert qdrant.count(QCOLL).count == 2  # points are NEVER deleted on a state move
    with engine.connect() as conn:
        act = conn.execute(select(acts).where(acts.c.act_ref == "legge-99001-2099")).one()
        assert act.vigenza == "abrogato"
        assert act.collection == COLL_ABROGATI
        assert act.file_path == f"{COLL_ABROGATI}/d3-test-atto-99001.md"
    points = act_points(qdrant, "legge-99001-2099")
    assert len(points) == 2
    assert all(p.payload["vigenza"] == "abrogato" for p in points)


@pytest.mark.db
def test_deleted_file_flips_vigenza_and_keeps_points(
    repo: Path, engine: Engine, qdrant: QdrantClient
) -> None:
    write_act(repo, COLL, "atto-99001.md", synthetic_act(99001))
    write_act(repo, COLL, "atto-99002.md", synthetic_act(99002))
    commit_all(repo)
    run_bootstrap(repo, engine, qdrant)

    (repo / COLL / "atto-99001.md").unlink()
    commit_all(repo)
    report = run_delta(repo, engine, qdrant)

    assert report.files_deleted == 1
    assert report.vigenza_flips == 1
    assert qdrant.count(QCOLL).count == 4  # historical points preserved
    with engine.connect() as conn:
        act = conn.execute(select(acts).where(acts.c.act_ref == "legge-99001-2099")).one()
        assert act.vigenza == "abrogato"  # conservative: deleted upstream -> abrogato
        other = conn.execute(select(acts).where(acts.c.act_ref == "legge-99002-2099")).one()
        assert other.vigenza == "vigente"  # untouched
        gone = conn.execute(
            select(ingestion_progress).where(
                ingestion_progress.c.file_path == f"{COLL}/atto-99001.md"
            )
        ).all()
        assert gone == []  # the file no longer exists: progress row removed
    assert all(p.payload["vigenza"] == "abrogato" for p in act_points(qdrant, "legge-99001-2099"))
    assert all(p.payload["vigenza"] == "vigente" for p in act_points(qdrant, "legge-99002-2099"))


@pytest.mark.db
def test_owner_rename_with_edit_reclaims_ownership_and_reindexes(
    repo: Path, engine: Engine, qdrant: QdrantClient
) -> None:
    # R<100 (git mv + edit in ONE commit) decomposes into delete(old) +
    # upsert(new). The delete must RELEASE the act's ownership so the upsert
    # reclaims the same act_ref and re-indexes under the new path — without
    # the release the upsert hits the A7 dedup branch and the act is left
    # abrogato, pointing at the dead path, with stale text, unhealable by a
    # delta re-run (the progress sha matches).
    write_act(repo, COLL, "atto-99001.md", synthetic_act(99001, articles=3))
    commit_all(repo)
    run_bootstrap(repo, engine, qdrant)
    assert qdrant.count(QCOLL).count == 3

    git(repo, "mv", f"{COLL}/atto-99001.md", f"{COLL}/atto-rinominato-99001.md")
    new_path = repo / COLL / "atto-rinominato-99001.md"
    edited = new_path.read_text(encoding="utf-8").replace("Primo comma", "Comma riscritto")
    new_path.write_text(edited, encoding="utf-8")
    c2 = commit_all(repo)
    embedder = FakeEmbedder()
    report = run_delta(repo, engine, qdrant, embedder)

    assert report.status == "completed"
    assert report.files_deleted == 1  # the old path
    assert report.files_indexed == 1  # the new path, fully re-indexed
    assert report.files_dedup_skipped == 0  # NOT the A7 branch
    assert embedder.calls == [3]  # the edited content was re-embedded
    assert qdrant.count(QCOLL).count == 3  # deterministic ids: no stale points
    rel_new = f"{COLL}/atto-rinominato-99001.md"
    with engine.connect() as conn:
        act = conn.execute(select(acts).where(acts.c.act_ref == "legge-99001-2099")).one()
        assert act.vigenza == "vigente"  # the conservative flip was overwritten
        assert act.file_path == rel_new  # ownership transferred to the new path
        assert act.last_commit_sha == c2
        old_rows = conn.execute(
            select(ingestion_progress).where(
                ingestion_progress.c.file_path == f"{COLL}/atto-99001.md"
            )
        ).all()
        assert old_rows == []
        progress = conn.execute(
            select(ingestion_progress).where(ingestion_progress.c.file_path == rel_new)
        ).one()
        assert progress.commit_sha == file_blob_sha(new_path.read_bytes())
        assert progress.act_ref == "legge-99001-2099"
    points = act_points(qdrant, "legge-99001-2099")
    assert len(points) == 3
    for point in points:
        assert point.payload["vigenza"] == "vigente"
        assert point.payload["file_path"] == rel_new
        assert "Comma riscritto" in point.payload["text"]


@pytest.mark.db
def test_owner_delete_with_unrelated_add_keeps_conservative_flip(
    repo: Path, engine: Engine, qdrant: QdrantClient
) -> None:
    # Releasing ownership on delete must NOT weaken the conservative path:
    # when the same-range add derives a DIFFERENT act_ref, nothing reclaims
    # the deleted act and it stays abrogato with its points preserved.
    write_act(repo, COLL, "atto-99001.md", synthetic_act(99001))
    commit_all(repo)
    run_bootstrap(repo, engine, qdrant)

    (repo / COLL / "atto-99001.md").unlink()
    # Distinctive content so git's rename detection cannot pair the pair;
    # even if it did (R<100), the decomposition is the same delete+upsert.
    write_act(repo, COLL, "atto-99002.md", synthetic_act(99002, articles=3))
    commit_all(repo)
    report = run_delta(repo, engine, qdrant)

    assert report.status == "completed"
    assert report.files_deleted == 1
    assert report.files_indexed == 1  # the unrelated new act
    assert report.vigenza_flips == 1
    with engine.connect() as conn:
        gone = conn.execute(select(acts).where(acts.c.act_ref == "legge-99001-2099")).one()
        assert gone.vigenza == "abrogato"  # conservative flip intact
        assert gone.file_path == f"{COLL}/atto-99001.md"  # documented limitation
        added = conn.execute(select(acts).where(acts.c.act_ref == "legge-99002-2099")).one()
        assert added.vigenza == "vigente"
        assert added.file_path == f"{COLL}/atto-99002.md"
    old_points = act_points(qdrant, "legge-99001-2099")
    assert len(old_points) == 2  # historical points preserved
    assert all(p.payload["vigenza"] == "abrogato" for p in old_points)
    assert len(act_points(qdrant, "legge-99002-2099")) == 3


@pytest.mark.db
def test_duplicate_added_in_abrogati_flips_vigenza_without_new_points(
    repo: Path, engine: Engine, qdrant: QdrantClient
) -> None:
    write_act(repo, COLL, "atto-99001.md", synthetic_act(99001))
    commit_all(repo)
    run_bootstrap(repo, engine, qdrant)

    # A new file in the abrogati folder deriving an act_ref OWNED by another
    # file: A7 dedup (no chunks) + the vigenza exception, on Postgres AND on
    # the existing Qdrant points.
    write_act(repo, COLL_ABROGATI, "d3-test-atto-99001.md", synthetic_act(99001))
    commit_all(repo)
    report = run_delta(repo, engine, qdrant)

    assert report.files_indexed == 0
    assert report.files_dedup_skipped == 1
    assert report.vigenza_flips == 1
    assert qdrant.count(QCOLL).count == 2  # no duplicate points
    with engine.connect() as conn:
        act = conn.execute(select(acts).where(acts.c.act_ref == "legge-99001-2099")).one()
        assert act.vigenza == "abrogato"
        assert act.file_path == f"{COLL}/atto-99001.md"  # ownership unchanged
        dup = conn.execute(
            select(ingestion_progress).where(
                ingestion_progress.c.file_path == f"{COLL_ABROGATI}/d3-test-atto-99001.md"
            )
        ).one()
        assert dup.act_ref == "legge-99001-2099"
    assert all(p.payload["vigenza"] == "abrogato" for p in act_points(qdrant, "legge-99001-2099"))


@pytest.mark.db
def test_empty_delta_records_completed_run(
    repo: Path, engine: Engine, qdrant: QdrantClient
) -> None:
    write_act(repo, COLL, "atto-99001.md", synthetic_act(99001))
    commit_all(repo)
    boot = run_bootstrap(repo, engine, qdrant)

    # No new commit at all: HEAD == last successful commit_to.
    report = run_delta(repo, engine, qdrant)
    assert report.status == "completed"
    assert report.files_changed == 0
    assert report.commit_from == boot.commit_to
    assert report.commit_to == boot.commit_to
    with engine.connect() as conn:
        run = conn.execute(select(ingestion_runs).where(ingestion_runs.c.id == report.run_id)).one()
        assert run.kind == "delta"
        assert run.status == "completed"
        assert run.files_processed == 0
        assert run.errors == []

    # New commits but no relevant .md change: still an empty delta, and the
    # watermark (commit_to) advances so the change is never re-scanned.
    (repo / "README.md").write_text("aggiornamento non normativo")
    c3 = commit_all(repo)
    report2 = run_delta(repo, engine, qdrant)
    assert report2.files_changed == 0
    assert report2.commit_from == boot.commit_to
    assert report2.commit_to == c3
    assert qdrant.count(QCOLL).count == 2  # nothing touched


@pytest.mark.db
def test_per_file_error_is_recorded_and_run_continues(
    repo: Path, engine: Engine, qdrant: QdrantClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_act(repo, COLL, "atto-99001.md", synthetic_act(99001))
    commit_all(repo)
    run_bootstrap(repo, engine, qdrant)

    write_act(repo, COLL, "atto-99002.md", synthetic_act(99002))
    write_act(repo, COLL, "atto-99003.md", synthetic_act(99003))
    commit_all(repo)
    monkeypatch.setattr(delta_module, "chunk_act", crashing_chunk_act_for("atto-99002.md"))
    report = run_delta(repo, engine, qdrant)

    assert report.status == "completed"  # one bad file never aborts the delta
    assert report.files_indexed == 1
    assert len(report.errors) == 1
    assert report.errors[0]["file_path"] == f"{COLL}/atto-99002.md"
    assert "ValueError" in report.errors[0]["error"]
    with engine.connect() as conn:
        run = conn.execute(select(ingestion_runs).where(ingestion_runs.c.id == report.run_id)).one()
        assert run.status == "completed"
        assert run.errors == report.errors
        crashed = conn.execute(
            select(ingestion_progress).where(
                ingestion_progress.c.file_path == f"{COLL}/atto-99002.md"
            )
        ).all()
        assert crashed == []  # no checkpoint for the failed file


@pytest.mark.db
def test_range_starts_from_last_successful_run(
    repo: Path, engine: Engine, qdrant: QdrantClient
) -> None:
    write_act(repo, COLL, "atto-99001.md", synthetic_act(99001))
    commit_all(repo)
    boot = run_bootstrap(repo, engine, qdrant)

    write_act(repo, COLL, "atto-99002.md", synthetic_act(99002))
    c2 = commit_all(repo)
    report1 = run_delta(repo, engine, qdrant)
    assert (report1.commit_from, report1.commit_to) == (boot.commit_to, c2)

    # Decoys that the range query must ignore: a FAILED run with a bogus
    # commit_to and a completed run with no commit_to at all.
    with engine.begin() as conn:
        for values in (
            {"kind": "delta", "status": "failed", "commit_to": "deadbeef"},
            {"kind": "bootstrap", "status": "completed", "commit_to": None},
        ):
            run_id = conn.execute(
                ingestion_runs.insert()
                .values(started_at=func.now(), finished_at=func.now(), **values)
                .returning(ingestion_runs.c.id)
            ).scalar_one()
            engine.created_run_ids.append(run_id)  # type: ignore[attr-defined]

    write_act(repo, COLL, "atto-99003.md", synthetic_act(99003))
    c3 = commit_all(repo)
    report2 = run_delta(repo, engine, qdrant)
    assert report2.commit_from == report1.commit_to  # chained, decoys ignored
    assert report2.commit_to == c3
    assert report2.files_indexed == 1  # only the c2..c3 change


@pytest.mark.db
def test_pull_brings_in_new_upstream_commits(
    repo: Path, engine: Engine, qdrant: QdrantClient, tmp_path: Path
) -> None:
    write_act(repo, COLL, "atto-99001.md", synthetic_act(99001))
    commit_all(repo)
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(repo), str(clone)], check=True, capture_output=True)
    run_bootstrap(clone, engine, qdrant)

    write_act(repo, COLL, "atto-99002.md", synthetic_act(99002))  # upstream commit
    c2 = commit_all(repo)
    report = run_delta(clone, engine, qdrant, pull=True)

    assert report.commit_to == c2  # the pull moved the clone's HEAD
    assert report.files_indexed == 1
    with engine.connect() as conn:
        act = conn.execute(select(acts).where(acts.c.act_ref == "legge-99002-2099")).one()
    assert act.file_path == f"{COLL}/atto-99002.md"
