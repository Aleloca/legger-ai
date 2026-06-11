"""Qdrant indexing pipeline for a corpus collection (Task C3).

The pipeline scans ``{corpus_path}/{collection}/*.md`` (sorted, so runs are
deterministic), parses each act (B3), derives its canonical ref (B4), chunks
it (B5) and upserts dense + sparse vectors into one Qdrant collection per
embedder: ``norme_{embedder_slug}`` (``norme_bgem3``, ``norme_voyagelaw2``,
``norme_voyage4large``). The three collections are the benchmark substrate
for the C4 hybrid-search evaluation.

Schema (per collection)
=======================
- named dense vector ``dense``: size = embedder dim, cosine distance;
- named sparse vector ``bm25``: ``modifier=IDF`` (Qdrant computes the IDF
  server-side; documents carry fastembed ``Qdrant/bm25`` term frequencies,
  tokenized/stemmed for **Italian**);
- keyword payload indexes on ``vigenza``, ``act_type``, ``act_ref`` and
  ``article`` (the C4/E-phase filters).

Point ids are ``uuid5(NAMESPACE_URL, chunk.id)``: deterministic, so re-running
the indexer overwrites the same points (idempotent upserts) and a crashed run
is recoverable with a plain re-run. On a re-run the indexer *resumes* at lot
granularity: a lot whose point ids are all already present in Qdrant is
skipped (no embed call, no upsert), so an interrupted run only re-pays the
incomplete lot. ``--no-resume`` forces full re-embedding. The human-readable
chunk id is kept in the payload as ``chunk_id``; the payload also carries
``text`` verbatim — that is what the LLM receives as context at retrieval
time.

Failure contract
================
- Per-file parse/derive errors are logged and skipped; the run continues and
  the report lists them (index integrity over completeness of a single run).
- A failed embed call loses only the current upsert lot (see the
  VoyageEmbedder caller contract): the lot is retried once, then the run
  aborts cleanly — already-upserted lots are durable and a re-run skips them
  (lot-level resume), re-embedding only what is missing.
- A timed-out ``wait=true`` upsert (HNSW indexing pressure on a large
  collection) is retried twice with 5s/20s backoff — idempotent point ids
  make a retry safe even if the timed-out request landed — then the run
  aborts cleanly with the same resume story.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from qdrant_client import QdrantClient, models
from qdrant_client.http.exceptions import ResponseHandlingException

from legger.corpus.chunker import Chunk, chunk_act
from legger.corpus.parser import parse_act
from legger.corpus.refs import derive_act_ref, vigenza_from_path
from legger.retrieval.embedders import Embedder, get_embedder
from legger.settings import Settings

if TYPE_CHECKING:
    from fastembed import SparseTextEmbedding

logger = logging.getLogger(__name__)

#: Qdrant collection name prefix; the full name is ``norme_{embedder_slug}``.
COLLECTION_PREFIX = "norme"
DENSE_VECTOR = "dense"
SPARSE_VECTOR = "bm25"
#: fastembed sparse model + language: Italian stopwords and Snowball stemmer.
BM25_MODEL = "Qdrant/bm25"
BM25_LANGUAGE = "italian"
#: Payload fields with a keyword index (the retrieval-time filters).
KEYWORD_INDEXES = ("vigenza", "act_type", "act_ref", "article")
#: Chunks per embed+upsert lot. Caps the blast radius of a failed embed call
#: and keeps upsert request bodies small (~256 * (1024 floats + payload)).
LOT_SIZE = 256
#: REST timeout (seconds) for the *indexing* Qdrant client. The qdrant-client
#: default (5s) is too tight for ``wait=true`` upserts on a large collection:
#: HNSW indexing pressure spikes made two full-corpus bootstraps die with
#: ``ResponseHandlingException: timed out`` around 400k points. 120s covers
#: the observed indexing pauses. Search/eval paths construct their OWN client
#: with a short explicit timeout — retrieval latency must not silently grow
#: to 120s (see ``legger.retrieval.search.SEARCH_CLIENT_TIMEOUT_S``).
INDEXING_CLIENT_TIMEOUT_S = 120
#: Backoff (seconds) before each upsert retry; module-level so tests can
#: zero it out.
UPSERT_RETRY_BACKOFF_S: tuple[float, ...] = (5.0, 20.0)


def embedder_slug(name: str) -> str:
    """Embedder name -> collection slug: lowercase alphanumerics only."""
    slug = "".join(c for c in name.lower() if c.isalnum())
    if not slug:
        raise ValueError(f"Embedder name {name!r} yields an empty slug.")
    return slug


def qdrant_collection_name(embedder_name: str, suffix: str | None = None) -> str:
    """``norme_{slug}`` with an optional ``_{suffix}`` tail (for experiments)."""
    name = f"{COLLECTION_PREFIX}_{embedder_slug(embedder_name)}"
    return f"{name}_{suffix}" if suffix else name


def point_id(chunk_id: str) -> str:
    """Deterministic Qdrant point id for a chunk id (uuid5, URL namespace)."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_id))


def chunk_payload(chunk: Chunk) -> dict[str, Any]:
    """Chunk -> point payload: every Chunk field, with ``id`` as ``chunk_id``.

    ``text`` is deliberately included — it is the context the LLM sees at
    retrieval time; ``header`` feeds result rendering and citations.
    """
    payload = chunk.model_dump()
    payload["chunk_id"] = payload.pop("id")
    return payload


def ensure_collection(
    client: QdrantClient,
    name: str,
    dim: int,
    *,
    recreate: bool = False,
) -> None:
    """Create the collection (schema above) if missing; verify it otherwise.

    Idempotent: an existing collection with the right schema is kept (so a
    re-run only overwrites points); a schema mismatch raises instead of
    silently mixing vector spaces. ``recreate=True`` drops and recreates.
    Payload indexes are (re-)declared on every call — the operation is
    idempotent on Qdrant's side.
    """
    if client.collection_exists(name):
        if recreate:
            logger.info("dropping existing collection %s (--recreate)", name)
            client.delete_collection(name)
        else:
            _verify_schema(client, name, dim)
    if not client.collection_exists(name):
        client.create_collection(
            collection_name=name,
            vectors_config={
                DENSE_VECTOR: models.VectorParams(size=dim, distance=models.Distance.COSINE),
            },
            sparse_vectors_config={
                SPARSE_VECTOR: models.SparseVectorParams(modifier=models.Modifier.IDF),
            },
        )
        logger.info("created collection %s (dense dim=%d, sparse bm25/IDF)", name, dim)
    for fieldname in KEYWORD_INDEXES:
        client.create_payload_index(
            collection_name=name,
            field_name=fieldname,
            field_schema=models.PayloadSchemaType.KEYWORD,
        )


def _verify_schema(client: QdrantClient, name: str, dim: int) -> None:
    params = client.get_collection(name).config.params
    dense = (params.vectors or {}).get(DENSE_VECTOR) if isinstance(params.vectors, dict) else None
    if dense is None or dense.size != dim or dense.distance != models.Distance.COSINE:
        raise RuntimeError(
            f"Collection {name!r} exists with an incompatible dense vector config "
            f"(expected {DENSE_VECTOR!r}, size={dim}, cosine; got {dense!r}). "
            "Re-run with --recreate to drop and rebuild it."
        )
    if SPARSE_VECTOR not in (params.sparse_vectors or {}):
        raise RuntimeError(
            f"Collection {name!r} exists without the {SPARSE_VECTOR!r} sparse vector. "
            "Re-run with --recreate to drop and rebuild it."
        )


@dataclass
class IndexReport:
    """Outcome of one indexing run (returned by :func:`index_collection`)."""

    qdrant_collection: str
    files_total: int = 0
    files_indexed: int = 0
    chunks_indexed: int = 0
    chunks_skipped: int = 0
    elapsed_s: float = 0.0
    file_errors: list[tuple[str, str]] = field(default_factory=list)  # (rel_path, error)


def make_bm25_model() -> SparseTextEmbedding:
    """Construct the fastembed BM25 model (single factory for index + search).

    Index time and query time MUST use the same model + language, or query
    terms tokenize/stem differently from the indexed documents and the sparse
    branch silently degrades — hence one shared factory.
    """
    from fastembed import SparseTextEmbedding

    return SparseTextEmbedding(model_name=BM25_MODEL, language=BM25_LANGUAGE)


def _load_chunks(settings: Settings, corpus_collection: str) -> tuple[list[Chunk], IndexReport]:
    """Parse and chunk every act in the collection folder (resilient per file)."""
    report = IndexReport(qdrant_collection="")
    folder = settings.corpus_path / corpus_collection
    files = sorted(folder.glob("*.md"))
    if not files:
        raise FileNotFoundError(f"No .md files found in {folder}")
    report.files_total = len(files)
    chunks: list[Chunk] = []
    for path in files:
        rel_path = path.relative_to(settings.corpus_path).as_posix()
        try:
            act = parse_act(path)
            ref = derive_act_ref(act, rel_path)
            chunks.extend(
                chunk_act(
                    act,
                    ref,
                    vigenza=vigenza_from_path(rel_path),
                    collection=corpus_collection,
                    file_path=rel_path,
                )
            )
            report.files_indexed += 1
        except Exception as exc:  # per-file resilience: log, record, continue
            logger.exception("failed to parse/chunk %s", rel_path)
            report.file_errors.append((rel_path, f"{type(exc).__name__}: {exc}"))
    return chunks, report


def _embed_lot_with_retry(embedder: Embedder, texts: list[str], lot_no: int) -> list[list[float]]:
    """One dense embed call per lot, retried once; abort cleanly on the 2nd failure."""
    try:
        return embedder.embed_documents(texts)
    except Exception:
        logger.exception("embed call failed for lot %d; retrying once", lot_no)
    try:
        return embedder.embed_documents(texts)
    except Exception as exc:
        raise RuntimeError(
            f"Embedding lot {lot_no} failed twice ({type(exc).__name__}: {exc}). "
            "Aborting; already-upserted lots are durable and point ids are "
            "deterministic, so simply re-run the same command to resume."
        ) from exc


def _upsert_lot_with_retry(
    client: QdrantClient,
    collection_name: str,
    points: list[models.PointStruct],
    lot_no: int,
) -> None:
    """Upsert one lot, retrying timeouts twice (backoff 5s/20s); then abort.

    Mirrors :func:`_embed_lot_with_retry`. Under HNSW indexing pressure a
    large collection occasionally stalls a ``wait=true`` upsert past the REST
    timeout (``ResponseHandlingException: timed out``); a short backoff lets
    the indexer catch up. Point ids are deterministic, so a retried upsert is
    idempotent even if the timed-out request actually landed.

    ``wait=True`` is deliberate and must stay: the bootstrap durability
    contract (``ingestion_progress``/``acts`` rows written via ``on_durable``
    only after the chunks are durable in Qdrant) depends on the upsert ack
    meaning "persisted", not "queued". ``wait=False`` plus a final flush
    would let a crash record progress rows for chunks Qdrant never applied.
    """
    last_exc: Exception | None = None
    for attempt, backoff_s in enumerate((0.0, *UPSERT_RETRY_BACKOFF_S)):
        if backoff_s:
            time.sleep(backoff_s)
        try:
            client.upsert(collection_name=collection_name, points=points, wait=True)
            return
        except (ResponseHandlingException, TimeoutError) as exc:
            last_exc = exc
            if attempt < len(UPSERT_RETRY_BACKOFF_S):
                logger.warning(
                    "upsert of lot %d timed out (%s: %s); retry %d/%d in %.0fs",
                    lot_no,
                    type(exc).__name__,
                    exc,
                    attempt + 1,
                    len(UPSERT_RETRY_BACKOFF_S),
                    UPSERT_RETRY_BACKOFF_S[attempt],
                )
    raise RuntimeError(
        f"Upsert of lot {lot_no} failed {1 + len(UPSERT_RETRY_BACKOFF_S)} times "
        f"({type(last_exc).__name__}: {last_exc}). Aborting; already-upserted "
        "lots are durable and point ids are deterministic, so simply re-run "
        "the same command to resume."
    ) from last_exc


def _lot_already_indexed(client: QdrantClient, collection_name: str, ids: list[str]) -> bool:
    """True iff *all* ``ids`` already exist as points in the collection.

    Partial presence (an interrupted upsert) returns False so the lot is
    re-embedded and overwritten — idempotent point ids make that safe.
    """
    records = client.retrieve(
        collection_name=collection_name,
        ids=ids,
        with_payload=False,
        with_vectors=False,
    )
    return len(records) == len(ids)


def index_chunks(
    client: QdrantClient,
    collection_name: str,
    chunks: list[Chunk],
    embedder: Embedder,
    sparse_model: SparseTextEmbedding,
    *,
    lot_size: int = LOT_SIZE,
    resume: bool = True,
) -> tuple[int, int]:
    """Embed (dense + sparse) and upsert ``chunks`` in lots.

    With ``resume`` (the default) a lot whose point ids are all already in
    Qdrant is skipped — no embed call, no upsert. Returns
    ``(chunks_indexed, chunks_skipped)``.
    """
    total = len(chunks)
    lots = (total + lot_size - 1) // lot_size
    done = 0
    skipped = 0
    started = time.monotonic()
    for lot_no, start in enumerate(range(0, total, lot_size), start=1):
        lot = chunks[start : start + lot_size]
        ids = [point_id(chunk.id) for chunk in lot]
        if resume and _lot_already_indexed(client, collection_name, ids):
            skipped += len(lot)
            logger.info("lot %d/%d SKIPPED (already indexed)", lot_no, lots)
            continue
        texts = [chunk.text for chunk in lot]
        dense = _embed_lot_with_retry(embedder, texts, lot_no)
        sparse = list(sparse_model.embed(texts))
        points = [
            models.PointStruct(
                id=pid,
                vector={
                    DENSE_VECTOR: dense_vec,
                    SPARSE_VECTOR: models.SparseVector(
                        indices=sparse_vec.indices.tolist(),
                        values=sparse_vec.values.tolist(),
                    ),
                },
                payload=chunk_payload(chunk),
            )
            for pid, chunk, dense_vec, sparse_vec in zip(ids, lot, dense, sparse, strict=True)
        ]
        # wait=True is load-bearing: see _upsert_lot_with_retry's docstring.
        _upsert_lot_with_retry(client, collection_name, points, lot_no)
        done += len(lot)
        rate = done / max(time.monotonic() - started, 1e-9)
        logger.info(
            "lot %d/%d upserted — %d/%d chunks (%.1f chunks/s)", lot_no, lots, done, total, rate
        )
    return done, skipped


def index_collection(
    corpus_collection: str,
    embedder_name: str,
    *,
    settings: Settings | None = None,
    suffix: str | None = None,
    recreate: bool = False,
    lot_size: int = LOT_SIZE,
    resume: bool = True,
) -> IndexReport:
    """Index one corpus collection into ``norme_{embedder_slug}`` on Qdrant."""
    settings = settings or Settings()
    embedder = get_embedder(embedder_name)
    collection_name = qdrant_collection_name(embedder_name, suffix)
    started = time.monotonic()

    chunks, report = _load_chunks(settings, corpus_collection)
    report.qdrant_collection = collection_name
    logger.info(
        "parsed %d/%d files -> %d chunks (%d file errors)",
        report.files_indexed,
        report.files_total,
        len(chunks),
        len(report.file_errors),
    )

    client = QdrantClient(url=settings.qdrant_url, timeout=INDEXING_CLIENT_TIMEOUT_S)
    ensure_collection(client, collection_name, embedder.dim, recreate=recreate)
    report.chunks_indexed, report.chunks_skipped = index_chunks(
        client,
        collection_name,
        chunks,
        embedder,
        make_bm25_model(),
        lot_size=lot_size,
        resume=resume,
    )
    report.elapsed_s = time.monotonic() - started
    return report
