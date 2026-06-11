"""Git-diff-driven delta ingestion (Task D3, design §4.1).

After bootstrap (D2) the corpus evolves upstream as git commits. The delta
run mirrors those changes into Qdrant + Postgres without re-walking 288k
files: ``git pull --ff-only`` (skippable with ``--no-pull``), then
``git diff --name-status -z -M <from> <to>`` over the commit range, then one
targeted action per changed ``.md`` file. The run is recorded in
``ingestion_runs`` with ``kind='delta'`` and the real ``commit_from``/
``commit_to`` range.

Range determination
===================
``commit_from`` is the ``commit_to`` of the LAST SUCCESSFUL run (bootstrap
or delta, whichever is most recent by run id) — deliberately NOT
``HEAD@{1}``: the reflog dies with the checkout, lies after manual fetches
or multiple pulls between runs, and cannot survive a failed delta (a failed
run does not advance the watermark, so the next run retries the same range;
the file-level sha checkpoints make the retry cheap). With no previous
successful run the delta REFUSES to guess and asks for a bootstrap first.
``commit_to`` is the post-pull HEAD; an empty range (no commits, or no
relevant ``.md`` change) still records a completed 0-file run so the
watermark advances past non-normative commits.

Change semantics (per diff status)
==================================
- **A/M/T** — parse → derive → chunk → embed → upsert via the bootstrap lot
  pipeline (:class:`~legger.ingestion.bootstrap._LotFlusher`) with
  ``resume=False``: point ids are uuid5(chunk_id) and chunk ids are
  content-INdependent, so a modified file produces the SAME ids with
  different text — the lot-level "already indexed" skip would silently keep
  the stale text. A7 dedup applies against the EXISTING acts table: a file
  whose act_ref is owned by another path contributes no chunks (progress row
  only), with the abrogato/decaduto vigenza exception applied to Postgres
  AND to the act's existing Qdrant points (``set_payload``).
- **R100 (pure rename, identical blob)** — handled without re-embedding:
  the progress row moves to the new path (same content sha), and when the
  old path OWNED the act the acts row (file_path/collection/vigenza) and the
  act's point payloads are updated in place. A rename INTO the
  abrogati/decaduti folders is exactly the design §4.1 case: vigenza flips
  on Postgres and on every point payload, and the points are NEVER deleted
  (they serve temporal versioning). A rename of a non-owner duplicate only
  moves its progress row (plus the A7 vigenza exception).
- **R<100 (rename + edit)** — decomposed into delete(old) + upsert(new):
  the content changed, so the new path goes through the full pipeline. The
  delete releases the act's ownership (processed first, see below), so the
  upsert of the new path reclaims the same act_ref and re-indexes it under
  the new location — the re-upserted points overwrite the conservative
  abrogato flip and the stale-id cleanup removes any chunks the new text no
  longer produces.
- **D (deleted upstream)** — conservative: the act flips to ``abrogato``
  (Postgres + point payloads) when the deleted path owned it and it was
  ``vigente``; an already abrogato/decaduto act keeps its state. Points are
  kept (temporal versioning); the progress row is deleted because the file
  no longer exists, and the act's ownership is released within the run so a
  same-range upsert deriving the same act_ref can reclaim it (deletes are
  always processed before upserts). Deleting a non-owner duplicate touches
  nothing but its progress row.

Stale-point cleanup vs historical preservation
==============================================
These are different things and the distinction is load-bearing:

- Vigenza transitions (move to abrogati, upstream delete) keep every point —
  an abrogated act must remain retrievable (filtered, labeled) and its text
  history lives in the corpus git, reachable for temporal queries (§4.5).
- A *modified* file that now yields FEWER chunks (e.g. an article removed by
  consolidation) leaves orphan points whose ids the new chunking no longer
  produces. Those are NOT history — they are stale fragments of the CURRENT
  consolidated text (old versions live as git commits, not as leftover
  points) and they would surface in retrieval as ghost articles. The delta
  deletes exactly those ids: points of THAT act_ref not in the new id set,
  computed after the new chunks are durable.

Durability mirrors bootstrap: Qdrant maintenance (stale deletes, payload
flips) happens BEFORE the Postgres progress/acts rows are written, so a
crash between the two is healed by re-running the same range (the failed
run never advanced the watermark). Per-file errors are recorded in the
run's ``errors`` JSONB and the run continues; note that, unlike bootstrap,
a file that errors in a COMPLETED delta is not retried by the next delta
(the watermark advances past it) — the error record is the alerting hook
for D4.

Known conservative limitations (documented trade-offs):
- a rename whose new content derives a DIFFERENT act_ref leaves the old
  acts row pointing at the vanished path (vigenza untouched);
- deleting the owner file flips the act to abrogato even when a duplicate
  copy still exists in a vigente collection (the duplicate's progress row
  blocks re-evaluation); both heal only under a FROM-SCRATCH re-bootstrap
  (an incremental re-bootstrap resume-skips on the matching progress sha).
"""

from __future__ import annotations

import logging
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from qdrant_client import models
from sqlalchemy import Engine, func, select

from legger.corpus.chunker import chunk_act
from legger.corpus.refs import vigenza_from_path
from legger.db import (
    acts,
    get_engine,
    ingestion_progress,
    ingestion_runs,
    set_vigenza,
    upsert_act,
    upsert_progress,
)
from legger.ingestion.bootstrap import (
    DEFAULT_QDRANT_COLLECTION,
    _close_run,
    _derive,
    _FileTask,
    _Interrupted,
    _LotFlusher,
    _open_run,
    _SignalGuard,
    corpus_head_sha,
    file_blob_sha,
)
from legger.retrieval.index import (
    INDEXING_CLIENT_TIMEOUT_S,
    LOT_SIZE,
    ensure_collection,
    make_bm25_model,
    point_id,
)
from legger.settings import Settings

if TYPE_CHECKING:
    from qdrant_client import QdrantClient

    from legger.retrieval.embedders import Embedder

logger = logging.getLogger(__name__)

_SCROLL_PAGE = 1000


class DeltaRefusedError(RuntimeError):
    """The delta cannot run at all (no previous run, git pull/diff failure)."""


# ---------------------------------------------------------------------------
# Git plumbing
# ---------------------------------------------------------------------------


def _git(corpus_path: Path, *args: str, timeout: int = 600) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(corpus_path), *args],
            capture_output=True,
            text=True,
            check=True,
            timeout=timeout,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        raise DeltaRefusedError(f"git {args[0]} fallito in {corpus_path}: {detail}") from exc
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DeltaRefusedError(f"git {args[0]} fallito in {corpus_path}: {exc}") from exc
    return proc.stdout


def _pull(corpus_path: Path) -> None:
    old_head = corpus_head_sha(corpus_path)
    logger.info("git pull --ff-only in %s (HEAD %s)", corpus_path, _short(old_head))
    _git(corpus_path, "pull", "--ff-only")


def _short(sha: str | None) -> str:
    return sha[:12] if sha else "?"


@dataclass(frozen=True)
class Change:
    """One normalized corpus change: 'upsert' | 'delete' | 'move' (pure rename)."""

    status: str
    path: str  # the surviving path ('move': the new path)
    old_path: str | None = None  # 'move' only


def _relevant(path: str) -> bool:
    """Corpus act files only: ``.md`` inside a collection folder."""
    return path.endswith(".md") and "/" in path


def diff_changes(corpus_path: Path, commit_from: str, commit_to: str) -> list[Change]:
    """Normalized changes in ``commit_from..commit_to`` (working tree untouched).

    ``-z`` makes the parse robust to the spaces every collection folder
    contains; ``-M`` keeps git's rename detection explicit. An exact rename
    (identical blob, score R100) becomes a 'move'; a rename with edits is
    decomposed into delete(old) + upsert(new). Copies (Cxx) upsert the new
    path only. Unknown statuses fall back to 'upsert' (re-indexing is the
    safe direction).
    """
    if commit_from == commit_to:
        return []
    raw = _git(corpus_path, "diff", "--name-status", "-z", "-M", commit_from, commit_to)
    tokens = raw.split("\0")
    changes: list[Change] = []
    i = 0
    while i < len(tokens):
        status = tokens[i]
        if not status:  # trailing NUL
            i += 1
            continue
        code = status[0]
        if code in ("R", "C"):
            old_path, path = tokens[i + 1], tokens[i + 2]
            i += 3
        else:
            old_path, path = None, tokens[i + 1]
            i += 2
        if code == "R":
            if _relevant(old_path) and _relevant(path) and status[1:] == "100":
                changes.append(Change("move", path, old_path))
                continue
            if _relevant(old_path):  # type: ignore[arg-type]
                changes.append(Change("delete", old_path))  # type: ignore[arg-type]
            if _relevant(path):
                changes.append(Change("upsert", path))
        elif code == "D":
            if _relevant(path):
                changes.append(Change("delete", path))
        else:  # A, M, T, C and anything exotic: (re)index the surviving path
            if _relevant(path):
                changes.append(Change("upsert", path))
    return changes


# ---------------------------------------------------------------------------
# Range determination
# ---------------------------------------------------------------------------


def _last_successful_commit(engine: Engine) -> str | None:
    """``commit_to`` of the most recent completed run that recorded one."""
    with engine.connect() as conn:
        return conn.execute(
            select(ingestion_runs.c.commit_to)
            .where(
                ingestion_runs.c.status == "completed",
                ingestion_runs.c.commit_to.is_not(None),
            )
            .order_by(ingestion_runs.c.id.desc())
            .limit(1)
        ).scalar_one_or_none()


# ---------------------------------------------------------------------------
# Qdrant helpers (payload flips + stale-point cleanup)
# ---------------------------------------------------------------------------


def _act_filter(act_ref: str) -> models.Filter:
    return models.Filter(
        must=[models.FieldCondition(key="act_ref", match=models.MatchValue(value=act_ref))]
    )


def _set_points_payload(
    client: QdrantClient, collection: str, act_ref: str, payload: dict[str, Any]
) -> None:
    client.set_payload(
        collection_name=collection, payload=payload, points=_act_filter(act_ref), wait=True
    )


def _act_point_ids(client: QdrantClient, collection: str, act_ref: str) -> set[str]:
    ids: set[str] = set()
    offset = None
    while True:
        points, offset = client.scroll(
            collection,
            scroll_filter=_act_filter(act_ref),
            limit=_SCROLL_PAGE,
            offset=offset,
            with_payload=False,
            with_vectors=False,
        )
        ids.update(str(point.id) for point in points)
        if offset is None:
            return ids


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


@dataclass
class DeltaReport:
    """Outcome of one delta run."""

    status: str  # 'completed' | 'failed'
    commit_from: str
    commit_to: str | None = None
    qdrant_collection: str | None = None
    run_id: int | None = None
    files_changed: int = 0  # relevant .md changes in the range
    files_indexed: int = 0  # files (re-)embedded and upserted
    files_moved: int = 0  # pure renames applied without re-embedding
    files_deleted: int = 0  # upstream deletions applied
    files_resume_skipped: int = 0  # diffed files whose progress sha already matches
    files_dedup_skipped: int = 0  # act_ref owned by another file (A7)
    chunks_indexed: int = 0
    chunks_skipped: int = 0
    stale_points_deleted: int = 0  # orphan ids of re-indexed acts (NOT history)
    vigenza_flips: int = 0
    errors: list[dict] = field(default_factory=list)  # {"file_path", "error"}
    note: str | None = None
    elapsed_s: float = 0.0

    @property
    def files_processed(self) -> int:
        """Changes applied, as persisted in ``ingestion_runs.files_processed``."""
        return self.files_indexed + self.files_moved + self.files_deleted

    @property
    def files_skipped(self) -> int:
        return self.files_resume_skipped + self.files_dedup_skipped


# ---------------------------------------------------------------------------
# Delta entry point
# ---------------------------------------------------------------------------


def delta(
    *,
    embedder_name: str = "voyage-4-large",
    qdrant_collection: str = DEFAULT_QDRANT_COLLECTION,
    pull: bool = True,
    settings: Settings | None = None,
    engine: Engine | None = None,
    qdrant_client: QdrantClient | None = None,
    embedder: Embedder | None = None,
    sparse_model: Any | None = None,
    lot_size: int = LOT_SIZE,
) -> DeltaReport:
    """Run one delta ingestion (module docstring has the full contract).

    Raises :class:`DeltaRefusedError` when the delta cannot run at all (no
    previous successful run, pull/diff failure); per-file problems never
    abort the run. ``engine``/``qdrant_client``/``embedder``/``sparse_model``
    are injectable for tests; production callers pass none of them.
    """
    settings = settings or Settings()
    corpus_path = settings.corpus_path
    started = time.monotonic()
    engine = engine or get_engine(settings)

    commit_from = _last_successful_commit(engine)
    if commit_from is None:
        raise DeltaRefusedError(
            "nessuna run completata in ingestion_runs: il delta non ha un commit di "
            "partenza. Esegui prima `legger ingest bootstrap`."
        )
    if pull:
        _pull(corpus_path)
    commit_to = corpus_head_sha(corpus_path)
    if commit_to is None:
        raise DeltaRefusedError(
            f"impossibile leggere l'HEAD del corpus in {corpus_path}: non e' un checkout git?"
        )

    changes = diff_changes(corpus_path, commit_from, commit_to)
    report = DeltaReport(
        status="completed",
        commit_from=commit_from,
        commit_to=commit_to,
        qdrant_collection=qdrant_collection,
        files_changed=len(changes),
    )

    if not changes:
        run_id = _open_run(engine, commit_to, kind="delta", commit_from=commit_from)
        report.run_id = run_id
        report.elapsed_s = time.monotonic() - started
        _close_run(engine, run_id, report)
        logger.info(
            "delta run #%d %s..%s: corpus aggiornato, nessuna modifica",
            run_id,
            _short(commit_from),
            _short(commit_to),
        )
        return report

    moves = sorted(
        ((c.old_path, c.path) for c in changes if c.status == "move"), key=lambda m: m[1]
    )
    deletes = sorted(c.path for c in changes if c.status == "delete")
    upsert_paths = {c.path for c in changes if c.status == "upsert"}

    from qdrant_client import QdrantClient

    client = qdrant_client or QdrantClient(
        url=settings.qdrant_url, timeout=INDEXING_CLIENT_TIMEOUT_S
    )
    if upsert_paths or moves:  # a move can fall back to a re-index
        from legger.retrieval.embedders import get_embedder

        embedder = embedder or get_embedder(embedder_name)
        sparse_model = sparse_model or make_bm25_model()
        ensure_collection(client, qdrant_collection, embedder.dim)

    # Checkpoint state, loaded BEFORE the run row is opened (a failure here
    # cannot leak a dangling 'running' row). Same maps as bootstrap, plus the
    # progress act_ref (moves) and the file_path -> act_ref ownership reverse
    # map (deletes).
    with engine.connect() as conn:
        progress_sha: dict[str, str] = {}
        progress_ref: dict[str, str | None] = {}
        for file_path, sha, act_ref in conn.execute(
            select(
                ingestion_progress.c.file_path,
                ingestion_progress.c.commit_sha,
                ingestion_progress.c.act_ref,
            )
        ):
            progress_sha[file_path] = sha
            progress_ref[file_path] = act_ref
        owner_of: dict[str, str] = {}
        vigenza_of: dict[str, str] = {}
        act_of_owner_path: dict[str, str] = {}
        for act_ref, file_path, vigenza in conn.execute(
            select(acts.c.act_ref, acts.c.file_path, acts.c.vigenza)
        ):
            owner_of[act_ref] = file_path
            vigenza_of[act_ref] = vigenza
            act_of_owner_path[file_path] = act_ref

    run_id = _open_run(engine, commit_to, kind="delta", commit_from=commit_from)
    report.run_id = run_id
    logger.info(
        "delta run #%d %s..%s: %d modifiche (%d upsert, %d move, %d delete), qdrant collection %r",
        run_id,
        _short(commit_from),
        _short(commit_to),
        len(changes),
        len(upsert_paths),
        len(moves),
        len(deletes),
        qdrant_collection,
    )

    new_point_ids: dict[str, set[str]] = {}  # file_path -> point ids of its new chunks

    def on_durable(tasks: list[_FileTask]) -> None:
        # Qdrant maintenance FIRST, Postgres rows AFTER: progress is recorded
        # only once Qdrant fully reflects the file, so a crash in between is
        # healed by re-running the same range.
        for task in tasks:
            if task.kind == "index":
                stale = (
                    _act_point_ids(client, qdrant_collection, task.act_ref)
                    - new_point_ids[task.file_path]
                )
                if stale:
                    client.delete(
                        qdrant_collection,
                        points_selector=models.PointIdsList(points=sorted(stale)),
                        wait=True,
                    )
                    report.stale_points_deleted += len(stale)
                    logger.info(
                        "%s: %d punti stantii eliminati (chunk non piu' prodotti da %s)",
                        task.act_ref,
                        len(stale),
                        task.file_path,
                    )
            elif task.vigenza_override is not None:
                _set_points_payload(
                    client, qdrant_collection, task.act_ref, {"vigenza": task.vigenza_override}
                )
        with engine.begin() as conn:
            for task in tasks:
                if task.kind == "index":
                    if task.act is None or task.ref is None:
                        raise RuntimeError(f"index task without act/ref: {task.file_path}")
                    upsert_act(
                        conn,
                        act_ref=task.ref.act_ref,
                        act_type=task.ref.act_type,
                        title=task.act.title,
                        collection=task.collection or "",
                        vigenza=task.vigenza or "vigente",
                        file_path=task.file_path,
                        last_commit_sha=commit_to,
                        number=task.ref.number,
                        year=task.ref.year,
                        date_pub=task.ref.date,
                        source=task.ref.source,
                    )
                    report.files_indexed += 1
                else:
                    if task.vigenza_override is not None:
                        if set_vigenza(
                            conn, task.act_ref, task.vigenza_override, last_commit_sha=commit_to
                        ):
                            report.vigenza_flips += 1
                        else:
                            logger.warning(
                                "vigenza override %s -> %s: nessuna riga acts",
                                task.act_ref,
                                task.vigenza_override,
                            )
                    report.files_dedup_skipped += 1
                upsert_progress(
                    conn, file_path=task.file_path, commit_sha=task.sha, act_ref=task.act_ref
                )

    flusher = _LotFlusher(
        client, qdrant_collection, embedder, sparse_model, lot_size, on_durable, resume=False
    )

    def apply_delete(path: str) -> None:
        act_ref = act_of_owner_path.get(path)
        flip = act_ref is not None and vigenza_of.get(act_ref) == "vigente"
        if flip:
            _set_points_payload(client, qdrant_collection, act_ref, {"vigenza": "abrogato"})
        with engine.begin() as conn:
            conn.execute(ingestion_progress.delete().where(ingestion_progress.c.file_path == path))
            if flip:
                set_vigenza(conn, act_ref, "abrogato", last_commit_sha=commit_to)
        if flip:
            vigenza_of[act_ref] = "abrogato"
            report.vigenza_flips += 1
            logger.info("%s eliminato a monte: %s -> abrogato (punti conservati)", path, act_ref)
        if act_ref is not None:
            # The deleted path owned the act: release the in-run ownership so
            # a same-range upsert deriving the same act_ref (R<100 rename,
            # delete + replacement add) reclaims it and runs the full index
            # pipeline instead of the A7 dedup branch. The re-upsert then
            # overwrites the conservative flip (acts row, point payloads) and
            # the stale-id cleanup handles any chunk shrinkage. Deletes run
            # BEFORE moves and upserts (see the loop below), so the release is
            # always visible to the reclaiming upsert.
            owner_of.pop(act_ref, None)
            act_of_owner_path.pop(path, None)
        progress_sha.pop(path, None)
        progress_ref.pop(path, None)
        report.files_deleted += 1

    def apply_move(old_path: str, new_path: str) -> bool:
        """Apply a pure rename without re-embedding; False -> re-index new_path."""
        act_ref = progress_ref.get(old_path)
        sha = progress_sha.get(old_path)
        if act_ref is None or sha is None or act_ref not in owner_of:
            # The old path was never durably ingested (or its act row is
            # missing): drop the stale progress row and re-index the new path.
            with engine.begin() as conn:
                conn.execute(
                    ingestion_progress.delete().where(ingestion_progress.c.file_path == old_path)
                )
            progress_sha.pop(old_path, None)
            progress_ref.pop(old_path, None)
            return False
        new_collection = new_path.split("/", 1)[0]
        new_vigenza = vigenza_from_path(new_path)
        if owner_of[act_ref] == old_path:
            # Owner moved: mirror the new location (and state folder) on the
            # acts row and on every existing point payload. Points are kept.
            flipped = vigenza_of.get(act_ref) != new_vigenza
            _set_points_payload(
                client,
                qdrant_collection,
                act_ref,
                {"file_path": new_path, "collection": new_collection, "vigenza": new_vigenza},
            )
            with engine.begin() as conn:
                conn.execute(
                    acts.update()
                    .where(acts.c.act_ref == act_ref)
                    .values(
                        file_path=new_path,
                        collection=new_collection,
                        vigenza=new_vigenza,
                        last_commit_sha=commit_to,
                        last_updated=func.now(),
                    )
                )
                conn.execute(
                    ingestion_progress.delete().where(ingestion_progress.c.file_path == old_path)
                )
                upsert_progress(conn, file_path=new_path, commit_sha=sha, act_ref=act_ref)
            owner_of[act_ref] = new_path
            vigenza_of[act_ref] = new_vigenza
            act_of_owner_path.pop(old_path, None)
            act_of_owner_path[new_path] = act_ref
            if flipped:
                report.vigenza_flips += 1
                logger.info(
                    "%s -> %s: vigenza %s (punti conservati)", old_path, new_path, new_vigenza
                )
        else:
            # A duplicate copy moved: only its progress row follows, plus the
            # A7 exception when it lands in a state folder.
            override = new_vigenza != "vigente" and vigenza_of.get(act_ref) != new_vigenza
            if override:
                _set_points_payload(client, qdrant_collection, act_ref, {"vigenza": new_vigenza})
            with engine.begin() as conn:
                conn.execute(
                    ingestion_progress.delete().where(ingestion_progress.c.file_path == old_path)
                )
                upsert_progress(conn, file_path=new_path, commit_sha=sha, act_ref=act_ref)
                if override:
                    set_vigenza(conn, act_ref, new_vigenza, last_commit_sha=commit_to)
            if override:
                vigenza_of[act_ref] = new_vigenza
                report.vigenza_flips += 1
        progress_sha[new_path] = sha
        progress_ref[new_path] = act_ref
        progress_sha.pop(old_path, None)
        progress_ref.pop(old_path, None)
        report.files_moved += 1
        return True

    try:
        with _SignalGuard() as guard:

            def check_interrupt() -> None:
                if guard.received is not None:
                    raise _Interrupted(signal.Signals(guard.received).name)

            # ORDER MATTERS: deletes first, so an owner-file delete releases
            # ownership before any upsert in the same range tries to reclaim
            # the act_ref (R<100 rename of the owner file).
            for path in deletes:
                check_interrupt()
                apply_delete(path)
            for old_path, new_path in moves:
                check_interrupt()
                if not apply_move(old_path, new_path):
                    upsert_paths.add(new_path)

            for rel_path in sorted(upsert_paths):
                check_interrupt()
                try:
                    raw = (corpus_path / rel_path).read_bytes()
                except OSError as exc:
                    logger.exception("cannot read %s", rel_path)
                    report.errors.append(
                        {"file_path": rel_path, "error": f"{type(exc).__name__}: {exc}"}
                    )
                    continue
                sha = file_blob_sha(raw)
                if progress_sha.get(rel_path) == sha:
                    report.files_resume_skipped += 1
                    continue
                try:
                    act, ref, vigenza, collection = _derive(raw, rel_path)
                except Exception as exc:
                    logger.exception("failed to parse/derive %s", rel_path)
                    report.errors.append(
                        {"file_path": rel_path, "error": f"{type(exc).__name__}: {exc}"}
                    )
                    continue
                owner = owner_of.get(ref.act_ref)
                if owner is not None and owner != rel_path:
                    # A7 dedup against the existing index; the state-folder
                    # vigenza exception flips Postgres AND the point payloads.
                    override = None
                    if vigenza != "vigente" and vigenza_of.get(ref.act_ref) != vigenza:
                        override = vigenza
                        vigenza_of[ref.act_ref] = vigenza
                    flusher.add(
                        _FileTask(rel_path, sha, ref.act_ref, "dup", vigenza_override=override),
                        [],
                    )
                    continue
                try:
                    chunks = chunk_act(
                        act, ref, vigenza=vigenza, collection=collection, file_path=rel_path
                    )
                except Exception as exc:
                    logger.exception("failed to chunk %s", rel_path)
                    report.errors.append(
                        {"file_path": rel_path, "error": f"{type(exc).__name__}: {exc}"}
                    )
                    continue
                owner_of[ref.act_ref] = rel_path
                vigenza_of[ref.act_ref] = vigenza
                act_of_owner_path[rel_path] = ref.act_ref
                new_point_ids[rel_path] = {point_id(chunk.id) for chunk in chunks}
                flusher.add(
                    _FileTask(
                        rel_path,
                        sha,
                        ref.act_ref,
                        "index",
                        act=act,
                        ref=ref,
                        vigenza=vigenza,
                        collection=collection,
                    ),
                    chunks,
                )
            flusher.finish()
    except _Interrupted as exc:
        report.status = "failed"
        report.note = f"interrupted by {exc} before completing; re-run to resume"
        logger.warning("delta run #%d interrotta (%s); il range verra' ritentato", run_id, exc)
        try:
            flusher.finish()  # drain: every parsed file becomes durable
        except Exception:
            logger.exception("final flush after interrupt failed; the re-run will re-pay it")
    except Exception as exc:
        report.status = "failed"
        report.note = f"fatal: {type(exc).__name__}: {exc}"
        logger.exception("delta run #%d fallita; i file durevoli restano checkpointati", run_id)
    except BaseException as exc:  # hard kill: stay honest
        report.status = "failed"
        report.note = f"aborted by {type(exc).__name__}; re-run to resume"
        logger.warning("delta run #%d abortita da %s", run_id, type(exc).__name__)
        raise
    finally:
        report.chunks_indexed = flusher.chunks_indexed
        report.chunks_skipped = flusher.chunks_skipped
        report.elapsed_s = time.monotonic() - started
        try:
            _close_run(engine, run_id, report)
        except Exception:
            logger.exception("could not close run row #%d (status %s)", run_id, report.status)
        logger.info(
            "delta run #%d %s: %d indicizzati, %d spostati, %d eliminati, %d skip "
            "(%d resume + %d dedup), %d chunk indicizzati, %d punti stantii eliminati, "
            "%d flip di vigenza, %d errori, %.0fs",
            run_id,
            report.status,
            report.files_indexed,
            report.files_moved,
            report.files_deleted,
            report.files_skipped,
            report.files_resume_skipped,
            report.files_dedup_skipped,
            report.chunks_indexed,
            report.stale_points_deleted,
            report.vigenza_flips,
            len(report.errors),
            report.elapsed_s,
        )
    return report
