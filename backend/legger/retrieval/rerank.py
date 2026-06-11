"""Cross-encoder reranking of hybrid-search hits (Task E3).

Separate stage on top of :func:`legger.retrieval.search.hybrid_search` (per
the C4 guidance, search.py stays a pure query -> hits mapping): widen the
candidate pool (``hybrid_search(..., k=50)``) and let
``BAAI/bge-reranker-v2-m3`` re-score the (query, chunk text) pairs jointly —
a cross-encoder reads both texts together, so it can resolve matches that
the bi-encoder dense vectors and BM25 rank too low.

Scores are sigmoid-normalized (``normalize=True``) relevance in [0, 1] and
replace ``SearchHit.score`` on the returned hits (the RRF fusion score is no
longer meaningful after reranking); input hits are not mutated.

Device policy (this Intel Mac wedged on MPS with FlagEmbedding models — see
embedders.py): the ``LEGGER_RERANK_DEVICE`` env var (cpu|mps|cuda) overrides
everything; otherwise **cpu on darwin** (the workload is small, ~50 pairs per
query) and the usual auto-detection (cuda > mps > cpu) elsewhere.

The ~2.3GB model is loaded lazily on the first :func:`rerank` call, as a
module-level singleton with double-checked locking (same pattern as the BM25
query model in search.py).

Measured impact (eval over norme_voyage4large, 30 queries — see
``backend/eval/results``): the recall@10 delta drives the default of
``Settings.rerank_enabled`` per the plan's decision rule (delta < 3 points
=> reranking stays off by default).
"""

from __future__ import annotations

import logging
import sys
import threading
from typing import TYPE_CHECKING, Any

from legger.retrieval.embedders import _detect_device

if TYPE_CHECKING:
    from legger.retrieval.search import SearchHit

logger = logging.getLogger(__name__)

RERANKER_MODEL_ID = "BAAI/bge-reranker-v2-m3"

#: Token window per (query, passage) pair. The model supports 8192 but
#: cross-encoder attention is quadratic and this runs on CPU: 1024 keeps
#: ~the first 2300 chars of each chunk (Italian legal text ~2.26 chars/token,
#: see embedders.py) — the article header + opening commi, where the signal
#: lives — at a tolerable per-query latency.
RERANK_MAX_LENGTH = 1024

#: Pairs per forward pass. Conservative for CPU inference (memory for a
#: 568M-param cross-encoder at 1024 tokens); 50 candidates = ~7 batches.
RERANK_BATCH_SIZE = 8

#: Devices accepted by the LEGGER_RERANK_DEVICE override (same set as
#: LEGGER_EMBED_DEVICE in embedders.py).
_VALID_DEVICES = ("cpu", "mps", "cuda")

_reranker: Any = None
_reranker_lock = threading.Lock()


def _rerank_device() -> str:
    """Pick the reranker device: env override, else cpu on darwin, else auto.

    ``LEGGER_RERANK_DEVICE`` (cpu|mps|cuda) is the per-process escape hatch,
    mirroring ``LEGGER_EMBED_DEVICE``; an invalid value logs a warning and is
    ignored. Without an override, darwin is pinned to **cpu**: FlagEmbedding
    inference on MPS has wedged indefinitely on Intel Macs (see embedders.py)
    and the reranking workload is small enough that CPU is fine. Elsewhere
    the embedders' auto-detection (cuda > mps > cpu) applies.
    """
    import os

    override = os.environ.get("LEGGER_RERANK_DEVICE")
    if override:
        if override in _VALID_DEVICES:
            return override
        logger.warning(
            "Ignoring invalid LEGGER_RERANK_DEVICE=%r (expected one of %s).",
            override,
            "|".join(_VALID_DEVICES),
        )
    if sys.platform == "darwin":
        return "cpu"
    return _detect_device()


def _make_reranker() -> Any:
    """Build the FlagReranker (separated out so tests can stub it)."""
    from FlagEmbedding import FlagReranker

    device = _rerank_device()
    logger.info("Loading reranker %s on device=%s ...", RERANKER_MODEL_ID, device)
    return FlagReranker(
        RERANKER_MODEL_ID,
        devices=device,
        use_fp16=device != "cpu",  # fp16 is slower than fp32 on CPU
        normalize=True,  # sigmoid scores in [0, 1]
        max_length=RERANK_MAX_LENGTH,
        batch_size=RERANK_BATCH_SIZE,
    )


def _get_reranker() -> Any:
    """Lazy singleton for the cross-encoder (thread-safe, double-checked)."""
    global _reranker
    if _reranker is None:
        with _reranker_lock:
            if _reranker is None:
                _reranker = _make_reranker()
    return _reranker


def rerank(query: str, hits: list[SearchHit], *, top_k: int = 10) -> list[SearchHit]:
    """Re-score ``hits`` against ``query`` with the cross-encoder; top-``top_k``.

    Returns NEW :class:`SearchHit` objects (inputs untouched) with ``score``
    replaced by the normalized cross-encoder relevance, sorted descending.
    The sort is stable, so ties keep the upstream (RRF) order. Empty ``hits``
    short-circuits without touching the model.

    Raises :class:`ValueError` on an empty/whitespace-only query, matching
    :func:`~legger.retrieval.search.hybrid_search`.
    """
    if not query.strip():
        raise ValueError("rerank: query must be a non-empty string")
    if not hits:
        return []
    pairs = [(query, hit.text) for hit in hits]
    scores = _get_reranker().compute_score(pairs)
    if not isinstance(scores, list):  # FlagReranker returns a bare float for one pair
        scores = [scores]
    rescored = [
        hit.model_copy(update={"score": float(score)})
        for hit, score in zip(hits, scores, strict=True)
    ]
    rescored.sort(key=lambda hit: hit.score, reverse=True)
    return rescored[:top_k]
