"""Full-corpus bootstrap ingestion with checkpoint/resume (Task D2).

Walks the corpus (all collections or a subset) in deterministic order
(sorted collection folders, sorted filenames), parses each act (B3), derives
its canonical ref (B4), chunks it (B5) and indexes the chunks into ONE Qdrant
collection via the C3 lot pipeline (:func:`legger.retrieval.index.index_chunks`:
uuid5 point ids, BM25 sparse, lot-level Qdrant resume). Postgres tracks the
run (``ingestion_runs``), per-file checkpoints (``ingestion_progress``) and
the per-act mirror (``acts``).

Resume layering (belt and braces)
=================================
1. **File level (Postgres, fast path)** -- a file whose ``ingestion_progress``
   row carries the same content sha is skipped without being parsed. The sha
   stored in ``ingestion_progress.commit_sha`` is the **git blob sha** of the
   file content (``sha1("blob <len>\\0" + bytes)``), NOT a commit sha: it is
   what ``git hash-object`` would print, it identifies the content exactly
   (so "file modified -> re-process" works even inside one corpus commit or
   with a dirty working tree), and D3 can recompute it from any git revision
   without touching the working tree.
2. **Lot level (Qdrant, belt-and-braces)** -- inside the indexing path,
   :func:`index_chunks` skips a lot whose point ids are all already present,
   so a crash between "chunks upserted" and "progress row written" only
   re-pays embedding for at most one lot on the next run.

Durability contract: the ``acts`` row and the ``ingestion_progress`` row of a
file are written only AFTER every chunk of that file has been upserted to
Qdrant (files are queued FIFO behind the lot buffer and released in order as
lots flush). A crash can therefore leave orphan points in Qdrant (harmless,
overwritten on re-run) but never a progress row whose chunks are missing.

Cross-collection dedup (A7)
===========================
The same act stored in several collections derives the same ``act_ref``
(~6.3k duplicate files corpus-wide). The FIRST file in deterministic order
wins: later files with an already-seen act_ref contribute no chunks, still
get a progress row (so they are never re-visited) and count as
``files_skipped``. Exception: when the later file's vigenza is ``abrogato``
or ``decaduto`` (the two state folders) and differs from the recorded one,
:func:`legger.db.set_vigenza` flips the act -- abrogato/decaduto wins over
vigente, never the other way round. Ownership is remembered across runs via
``acts.file_path``, so a *modified* owner file is re-processed rather than
dedup-skipped against itself.

Failure contract
================
- Per-file errors (unreadable file, unexpected parser crash) are logged,
  appended to the run's ``errors`` JSONB as ``{"file_path", "error"}`` and
  the run continues -- one weird file out of 288k must not abort bootstrap.
- A fatal error (embed call failed twice, Postgres down) closes the run row
  as ``failed`` with the error recorded; durable files stay checkpointed and
  a plain re-run resumes after them.
- SIGINT/SIGTERM: the first signal requests a graceful stop -- the current
  file finishes, the lot buffer is flushed (so every parsed file becomes
  durable), the run row closes as ``failed`` with an "interrupted" note.
  A second signal restores the default handler behaviour (hard kill).

``--dry-run`` parses and chunks only: no embedder construction (no API key
needed), no Qdrant, no Postgres. It reports file/chunk/char counts and an
estimated token total (chars / 2.26, the measured chars-per-token ratio of
Italian legal text under the Voyage tokenizer -- see
``legger.retrieval.embedders``); this is the C6 cost-estimate input.
"""

from __future__ import annotations

import hashlib
import logging
import signal
import subprocess
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import Engine, func, select

from legger.corpus.chunker import Chunk, chunk_act
from legger.corpus.parser import parse_act_text
from legger.corpus.refs import ActRef, derive_act_ref, vigenza_from_path
from legger.db import (
    acts,
    get_engine,
    ingestion_progress,
    ingestion_runs,
    set_vigenza,
    upsert_act,
    upsert_progress,
)
from legger.retrieval.index import (
    INDEXING_CLIENT_TIMEOUT_S,
    LOT_SIZE,
    ensure_collection,
    index_chunks,
    make_bm25_model,
)
from legger.settings import Settings

if TYPE_CHECKING:
    from qdrant_client import QdrantClient

    from legger.corpus.models import Act
    from legger.retrieval.embedders import Embedder

logger = logging.getLogger(__name__)

#: Default Qdrant collection for the production index (D2/D3/E phases).
DEFAULT_QDRANT_COLLECTION = "norme"
#: Measured chars-per-token of Italian legal text under the Voyage tokenizer
#: (see the token-counting notes in ``legger.retrieval.embedders``).
CHARS_PER_TOKEN = 2.26


def file_blob_sha(data: bytes) -> str:
    """Git blob sha1 of ``data`` -- identical to ``git hash-object`` output."""
    return hashlib.sha1(b"blob %d\x00%b" % (len(data), data)).hexdigest()


def corpus_head_sha(corpus_path: Path) -> str | None:
    """HEAD commit sha of the corpus checkout, or None when unavailable."""
    try:
        out = subprocess.run(
            ["git", "-C", str(corpus_path), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        return out.stdout.strip() or None
    except Exception:
        logger.warning("could not read the corpus HEAD sha from %s", corpus_path, exc_info=True)
        return None


def discover_files(corpus_path: Path, collections: list[str] | None = None) -> list[Path]:
    """All ``*.md`` files to ingest, in deterministic order.

    Collections (top-level corpus folders) are sorted by name, files inside
    each are sorted by name -- the same order on every run, which is what
    makes "first file wins" dedup deterministic. An explicitly requested
    collection that does not exist raises (typo guard); auto-discovery simply
    takes every top-level directory (hidden ones like ``.git`` excluded).
    """
    if collections is None:
        names = sorted(
            entry.name
            for entry in corpus_path.iterdir()
            if entry.is_dir() and not entry.name.startswith(".")
        )
    else:
        names = sorted(collections)
        for name in names:
            if not (corpus_path / name).is_dir():
                raise FileNotFoundError(f"Corpus collection folder not found: {corpus_path / name}")
    files: list[Path] = []
    for name in names:
        files.extend(sorted((corpus_path / name).glob("*.md")))
    return files


@dataclass
class BootstrapReport:
    """Outcome of one bootstrap run (real or dry)."""

    status: str  # 'completed' | 'failed' | 'dry-run'
    qdrant_collection: str | None = None
    run_id: int | None = None
    commit_to: str | None = None
    files_total: int = 0
    files_processed: int = 0  # files whose chunks are durably indexed
    files_resume_skipped: int = 0  # skipped via ingestion_progress sha match
    files_dedup_skipped: int = 0  # skipped via already-seen act_ref (A7)
    chunks_indexed: int = 0
    chunks_skipped: int = 0  # lot-level Qdrant resume skips
    est_chunks: int = 0  # dry-run: chunks that would be embedded
    total_chars: int = 0  # dry-run: total chars of chunk text
    est_tokens: int = 0  # dry-run: total_chars / CHARS_PER_TOKEN
    errors: list[dict] = field(default_factory=list)  # {"file_path", "error"}
    note: str | None = None  # interrupt / fatal-error note
    per_collection: dict[str, dict] = field(default_factory=dict)  # dry-run breakdown
    elapsed_s: float = 0.0

    @property
    def files_skipped(self) -> int:
        """Total skips, as persisted in ``ingestion_runs.files_skipped``."""
        return self.files_resume_skipped + self.files_dedup_skipped


# ---------------------------------------------------------------------------
# Signal handling: first SIGINT/SIGTERM requests a graceful stop, the second
# falls through to the original (default) handler.
# ---------------------------------------------------------------------------


class _SignalGuard:
    SIGNALS = (signal.SIGINT, signal.SIGTERM)

    def __init__(self) -> None:
        self.received: int | None = None
        self._original: dict[int, Any] = {}

    def __enter__(self) -> _SignalGuard:
        try:
            for sig in self.SIGNALS:
                self._original[sig] = signal.signal(sig, self._handle)
        except ValueError:  # not in the main thread: run without graceful stop
            logger.warning("cannot install signal handlers outside the main thread")
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._restore()

    def _handle(self, signum: int, frame: object) -> None:
        self.received = signum
        logger.warning(
            "received %s: finishing the current lot, then closing the run "
            "(send the signal again to kill immediately)",
            signal.Signals(signum).name,
        )
        self._restore()

    def _restore(self) -> None:
        for sig, original in self._original.items():
            signal.signal(sig, original)
        self._original = {}


# ---------------------------------------------------------------------------
# Lot buffer: accumulates chunks across files, flushes ~LOT_SIZE at a time,
# releases files (FIFO) once every one of their chunks is durable.
# ---------------------------------------------------------------------------


@dataclass
class _FileTask:
    """A file waiting behind the lot buffer for its chunks to become durable."""

    file_path: str  # corpus-relative posix path
    sha: str  # git blob sha of the content
    act_ref: str
    # 'index' tasks carry the act to upsert; 'dup' tasks only record progress
    # (plus the optional vigenza override).
    kind: str  # 'index' | 'dup'
    act: Act | None = None
    ref: ActRef | None = None
    vigenza: str | None = None
    collection: str | None = None
    vigenza_override: str | None = None  # dup whose state folder wins (A7)


class _LotFlusher:
    """Chunk accumulator over the C3 lot pipeline with FIFO file release.

    ``add(task, chunks)`` buffers the chunks; whenever the buffer holds a full
    lot it is embedded+upserted via :func:`index_chunks` (which applies the
    lot-level Qdrant resume check). After each flush, queued files whose
    chunks are now all durable are handed to ``on_durable`` -- in submission
    order, so an owner file is always released before its dedup followers.
    """

    def __init__(
        self,
        client: QdrantClient,
        collection_name: str,
        embedder: Embedder,
        sparse_model: Any,
        lot_size: int,
        on_durable: Any,  # callable(list[_FileTask]) -> None
    ) -> None:
        self._client = client
        self._collection_name = collection_name
        self._embedder = embedder
        self._sparse_model = sparse_model
        self._lot_size = lot_size
        self._on_durable = on_durable
        self._buffer: list[Chunk] = []
        self._queue: deque[list] = deque()  # [task, chunks_not_yet_flushed]
        self.chunks_indexed = 0
        self.chunks_skipped = 0

    def add(self, task: _FileTask, chunks: list[Chunk]) -> None:
        self._queue.append([task, len(chunks)])
        self._buffer.extend(chunks)
        while len(self._buffer) >= self._lot_size:
            self._flush(self._lot_size)
        self._release_ready()

    def finish(self) -> None:
        """Flush the partial tail lot and release every remaining file."""
        if self._buffer:
            self._flush(len(self._buffer))
        self._release_ready()
        if self._queue:
            raise RuntimeError(
                f"{len(self._queue)} files left pending after the final flush; "
                "the lot buffer accounting is broken"
            )

    def _flush(self, n: int) -> None:
        lot = self._buffer[:n]
        done, skipped = index_chunks(
            self._client,
            self._collection_name,
            lot,
            self._embedder,
            self._sparse_model,
            lot_size=n,
            resume=True,  # belt-and-braces lot-level Qdrant check
        )
        self.chunks_indexed += done
        self.chunks_skipped += skipped
        del self._buffer[:n]
        remaining = n
        for entry in self._queue:
            if remaining <= 0:
                break
            take = min(entry[1], remaining)
            entry[1] -= take
            remaining -= take

    def _release_ready(self) -> None:
        ready: list[_FileTask] = []
        while self._queue and self._queue[0][1] == 0:
            ready.append(self._queue.popleft()[0])
        if ready:
            self._on_durable(ready)


# ---------------------------------------------------------------------------
# Run row lifecycle
# ---------------------------------------------------------------------------


def _open_run(engine: Engine, commit_to: str | None) -> int:
    with engine.begin() as conn:
        return conn.execute(
            ingestion_runs.insert()
            .values(kind="bootstrap", status="running", commit_to=commit_to, started_at=func.now())
            .returning(ingestion_runs.c.id)
        ).scalar_one()


def _close_run(engine: Engine, run_id: int, report: BootstrapReport) -> None:
    errors = list(report.errors)
    if report.note:
        errors.append({"file_path": None, "error": report.note})
    with engine.begin() as conn:
        conn.execute(
            ingestion_runs.update()
            .where(ingestion_runs.c.id == run_id)
            .values(
                status=report.status,
                finished_at=func.now(),
                files_processed=report.files_processed,
                files_skipped=report.files_skipped,
                errors=errors,
            )
        )


class _Interrupted(Exception):
    """Internal: a graceful stop was requested via SIGINT/SIGTERM."""


# ---------------------------------------------------------------------------
# Bootstrap entry point
# ---------------------------------------------------------------------------


def bootstrap(
    *,
    collections: list[str] | None = None,
    embedder_name: str = "voyage-4-large",
    qdrant_collection: str = DEFAULT_QDRANT_COLLECTION,
    dry_run: bool = False,
    settings: Settings | None = None,
    engine: Engine | None = None,
    qdrant_client: QdrantClient | None = None,
    embedder: Embedder | None = None,
    sparse_model: Any | None = None,
    lot_size: int = LOT_SIZE,
) -> BootstrapReport:
    """Run the bootstrap ingestion (module docstring has the full contract).

    ``engine`` / ``qdrant_client`` / ``embedder`` / ``sparse_model`` are
    injectable for tests; production callers pass none of them.
    """
    settings = settings or Settings()
    files = discover_files(settings.corpus_path, collections)
    started = time.monotonic()

    if dry_run:
        report = _dry_run(settings, files)
        report.elapsed_s = time.monotonic() - started
        return report

    engine = engine or get_engine(settings)
    from qdrant_client import QdrantClient

    # Generous REST timeout: wait=true upserts stall under HNSW indexing
    # pressure on a large collection (see INDEXING_CLIENT_TIMEOUT_S).
    client = qdrant_client or QdrantClient(
        url=settings.qdrant_url, timeout=INDEXING_CLIENT_TIMEOUT_S
    )
    from legger.retrieval.embedders import get_embedder

    embedder = embedder or get_embedder(embedder_name)
    sparse_model = sparse_model or make_bm25_model()
    ensure_collection(client, qdrant_collection, embedder.dim)

    commit_to = corpus_head_sha(settings.corpus_path)

    # Checkpoint state: progress shas (file-level resume), act ownership and
    # current vigenza (cross-run dedup + the A7 vigenza exception). Loaded
    # BEFORE the run row is opened, so a failure here cannot leak a dangling
    # 'running' row.
    with engine.connect() as conn:
        progress_sha = {
            file_path: sha
            for file_path, sha in conn.execute(
                select(ingestion_progress.c.file_path, ingestion_progress.c.commit_sha)
            )
        }
        owner_of: dict[str, str] = {}
        vigenza_of: dict[str, str] = {}
        for act_ref, file_path, vigenza in conn.execute(
            select(acts.c.act_ref, acts.c.file_path, acts.c.vigenza)
        ):
            owner_of[act_ref] = file_path
            vigenza_of[act_ref] = vigenza

    run_id = _open_run(engine, commit_to)
    report = BootstrapReport(
        status="completed",
        qdrant_collection=qdrant_collection,
        run_id=run_id,
        commit_to=commit_to,
        files_total=len(files),
    )
    logger.info(
        "bootstrap run #%d: %d files, qdrant collection %r, embedder %s, corpus HEAD %s",
        run_id,
        len(files),
        qdrant_collection,
        embedder.name,
        commit_to,
    )

    def on_durable(tasks: list[_FileTask]) -> None:
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
                    report.files_processed += 1
                else:  # dup: FIFO release guarantees the owner's act row exists
                    if task.vigenza_override is not None:
                        updated = set_vigenza(
                            conn,
                            task.act_ref,
                            task.vigenza_override,
                            last_commit_sha=commit_to,
                        )
                        if not updated:
                            logger.warning(
                                "vigenza override %s -> %s: no acts row (owner errored?)",
                                task.act_ref,
                                task.vigenza_override,
                            )
                    report.files_dedup_skipped += 1
                upsert_progress(
                    conn, file_path=task.file_path, commit_sha=task.sha, act_ref=task.act_ref
                )

    flusher = _LotFlusher(client, qdrant_collection, embedder, sparse_model, lot_size, on_durable)

    try:
        with _SignalGuard() as guard:
            for n, path in enumerate(files, 1):
                if guard.received is not None:
                    raise _Interrupted(signal.Signals(guard.received).name)
                if n > 1 and (n - 1) % 1000 == 0:  # in-flight observability
                    logger.info(
                        "progress: files %d/%d (%d skipped), %d chunks indexed",
                        n - 1,
                        len(files),
                        report.files_skipped,
                        flusher.chunks_indexed,
                    )
                rel_path = path.relative_to(settings.corpus_path).as_posix()
                try:
                    raw = path.read_bytes()
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
                except Exception as exc:  # parser/deriver are lenient; belt-and-braces
                    logger.exception("failed to parse/derive %s", rel_path)
                    report.errors.append(
                        {"file_path": rel_path, "error": f"{type(exc).__name__}: {exc}"}
                    )
                    continue
                owner = owner_of.get(ref.act_ref)
                if owner is not None and owner != rel_path:
                    # A7 dedup: first processed file won; abrogato/decaduto
                    # from a state folder still overrides a vigente record.
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
                except Exception as exc:  # one unchunkable file must not block the corpus
                    logger.exception("failed to chunk %s", rel_path)
                    report.errors.append(
                        {"file_path": rel_path, "error": f"{type(exc).__name__}: {exc}"}
                    )
                    continue
                # Ownership is claimed only after chunking succeeded, so a
                # crashed owner never dedup-blocks its duplicates.
                owner_of[ref.act_ref] = rel_path
                vigenza_of[ref.act_ref] = vigenza
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
        logger.warning("run #%d interrupted (%s); progress is checkpointed", run_id, exc)
        try:
            # Drain the buffer so every already-parsed file becomes durable.
            flusher.finish()
        except Exception:
            logger.exception("final flush after interrupt failed; resume will re-pay it")
    except Exception as exc:
        report.status = "failed"
        report.note = f"fatal: {type(exc).__name__}: {exc}"
        logger.exception("run #%d failed; durable files stay checkpointed", run_id)
    except BaseException as exc:  # hard kill (2nd SIGINT, SystemExit): stay honest
        report.status = "failed"
        report.note = f"aborted by {type(exc).__name__}; re-run to resume"
        logger.warning("run #%d aborted by %s", run_id, type(exc).__name__)
        raise
    finally:
        report.chunks_indexed = flusher.chunks_indexed
        report.chunks_skipped = flusher.chunks_skipped
        report.elapsed_s = time.monotonic() - started
        try:
            _close_run(engine, run_id, report)
        except Exception:
            # Never mask the in-flight outcome: the dangling 'running' row is
            # cosmetic, the file-level checkpoints already carry the resume.
            logger.exception("could not close run row #%d (status %s)", run_id, report.status)
        logger.info(
            "run #%d %s: %d processed, %d skipped (%d resume + %d dedup), "
            "%d chunks indexed (%d already present), %d errors, %.0fs",
            run_id,
            report.status,
            report.files_processed,
            report.files_skipped,
            report.files_resume_skipped,
            report.files_dedup_skipped,
            report.chunks_indexed,
            report.chunks_skipped,
            len(report.errors),
            report.elapsed_s,
        )
    return report


def _derive(raw: bytes, rel_path: str) -> tuple[Act, ActRef, str, str]:
    """bytes -> (act, ref, vigenza, collection); mirrors parse_act's NUL handling."""
    text = raw.split(b"\x00", 1)[0].decode("utf-8", errors="replace")
    act = parse_act_text(text)
    ref = derive_act_ref(act, rel_path)
    vigenza = vigenza_from_path(rel_path)
    collection = rel_path.split("/", 1)[0]
    return act, ref, vigenza, collection


def _dry_run(settings: Settings, files: list[Path]) -> BootstrapReport:
    """Parse+chunk statistics only: no embedder, no Qdrant, no Postgres.

    Estimates the FULL from-scratch cost (``ingestion_progress`` is not
    consulted); in-run dedup IS simulated because dedup-skipped files would
    not be embedded. ``est_tokens = total_chars / 2.26`` (measured ratio,
    see the module docstring).
    """
    report = BootstrapReport(status="dry-run", files_total=len(files))
    seen_refs: set[str] = set()
    for path in files:
        rel_path = path.relative_to(settings.corpus_path).as_posix()
        collection = rel_path.split("/", 1)[0]
        stats = report.per_collection.setdefault(
            collection, {"files": 0, "files_dedup_skipped": 0, "chunks": 0, "chars": 0}
        )
        try:
            raw = path.read_bytes()
            act, ref, vigenza, _ = _derive(raw, rel_path)
        except Exception as exc:
            logger.exception("failed to parse/derive %s", rel_path)
            report.errors.append({"file_path": rel_path, "error": f"{type(exc).__name__}: {exc}"})
            continue
        if ref.act_ref in seen_refs:
            report.files_dedup_skipped += 1
            stats["files_dedup_skipped"] += 1
            continue
        try:
            chunks = chunk_act(
                act, ref, vigenza=vigenza, collection=collection, file_path=rel_path
            )
        except Exception as exc:  # mirror the real run: per-file error, keep going
            logger.exception("failed to chunk %s", rel_path)
            report.errors.append({"file_path": rel_path, "error": f"{type(exc).__name__}: {exc}"})
            continue
        seen_refs.add(ref.act_ref)
        chars = sum(len(chunk.text) for chunk in chunks)
        report.files_processed += 1
        report.est_chunks += len(chunks)
        report.total_chars += chars
        stats["files"] += 1
        stats["chunks"] += len(chunks)
        stats["chars"] += chars
    report.est_tokens = round(report.total_chars / CHARS_PER_TOKEN)
    return report
