"""Embedding providers for dense retrieval: local bge-m3 and the Voyage AI API.

Both implement the :class:`Embedder` protocol so the indexer (C3) and the
query path (C4) can swap providers behind a single ``get_embedder(name)`` call.
The sparse side of our hybrid retrieval comes from Qdrant BM25, so only dense
vectors are produced here (bge-m3's sparse/colbert outputs are not used).

Voyage AI findings (docs.voyageai.com, checked 2026-06-10)
----------------------------------------------------------
Current embedding models:

============== ======== ===================== =========== ============
model          context  dimensions            $/M tokens  free tokens
============== ======== ===================== =========== ============
voyage-4-large 32K      1024 (256/512/2048)   0.12        200M
voyage-4       32K      1024 (256/512/2048)   0.06        200M
voyage-4-lite  32K      1024 (256/512/2048)   0.02        200M
voyage-law-2   16K      1024                  0.12        50M
============== ======== ===================== =========== ============

Default for the C4 benchmark: **voyage-law-2** — the only legal-domain model,
at the same price as voyage-4-large; it is however an older generation, so the
benchmark should also try voyage-4-large (best general multilingual) before
the C6 go/no-go. Cost intel for C6: ~18.5k Codici chunks ≈ 7-8M tokens (well
inside the free tier); the full 300k+ chunk corpus ≈ 120M tokens ≈ $14 one-off
at $0.12/M.

API notes: ``input_type="query"|"document"`` is supported (the API prepends
retrieval-specific prompts); max 1,000 texts per request AND a per-request
total-token cap (docs.voyageai.com, checked 2026-06-10): 120K tokens for
voyage-law-2/voyage-4-large, 320K for voyage-4, 1M for voyage-4-lite. Both
constraints apply simultaneously, so batching must be token-aware (a 128-text
batch of ~8000-char legal chunks blew the 120K cap mid-run). The voyageai SDK
(0.4.0) retries natively via tenacity (exponential jitter, 1-16s) on rate
limit / service unavailable / timeout errors, but ``max_retries`` defaults to
0 — we enable it explicitly, so no custom backoff loop is needed.

Token counting for batching is EXACT and LOCAL: the SDK's
``Client.count_tokens`` (voyageai/_base.py, 0.4.0) loads the model's HF
tokenizer via ``tokenizers.Tokenizer.from_pretrained("voyageai/<model>")`` and
encodes locally — no API round-trip. We use the same tokenizer directly (the
SDK method only returns a sum, we need per-text counts). First use downloads
the tokenizer JSON from the HF Hub (then cached in ~/.cache/huggingface);
counting is cheap afterwards (~0.25ms/text measured). The previous
``len(text) // 3`` estimate UNDERESTIMATED Italian legal text — measured
~2.26 chars/token on Codici chunks (numbers, punctuation, ``((...))``
amendment markers) — and a batch hit 132,740 actual tokens vs the 120K cap.
If the tokenizer cannot be loaded (offline, HF down), batching falls back to
a conservative ``len(text) // 2`` estimate with a logged warning.

Local model notes
-----------------
bge-m3 runs through FlagEmbedding (``BGEM3FlagModel``). On macOS x86_64 the
newest available torch is 2.2.2, which forced three pins in pyproject:

- ``numpy<2``: torch 2.2 wheels are built against the NumPy 1.x ABI (the
  torch<->numpy bridge silently breaks under NumPy 2).
- ``transformers>=4.44,<4.50``: 4.50+ hard-refuse ``torch.load`` on torch<2.6
  (CVE-2025-32434 guard), and BAAI/bge-m3 ships only ``pytorch_model.bin``
  (no safetensors), so the checkpoint cannot load at all on this platform
  with newer transformers. We only ever load the trusted BAAI checkpoint.
- ``flagembedding>=1.3.5,<1.4``: 1.4.0 passes the transformers-v5 ``dtype=``
  kwarg, incompatible with the 4.x pin above.

Device is auto-detected (cuda > mps > cpu) so the same code runs on dev Macs
and the CPU-only VPS; the ``LEGGER_EMBED_DEVICE`` env var (cpu|mps|cuda)
overrides detection (see :func:`_detect_device`) — needed because MPS
inference has hung indefinitely in detached (nohup) runs on this Intel Mac.
Measured on this machine (MPS): ~9 docs/s at 1.5k chars, i.e. ~33 min for the
18.5k Codici chunks.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from legger.settings import Settings

if TYPE_CHECKING:
    from collections.abc import Iterator

    import voyageai

logger = logging.getLogger(__name__)

DEFAULT_VOYAGE_MODEL = "voyage-law-2"

#: Output dimensions per Voyage model (all current models default to 1024).
_VOYAGE_DIMS = {
    "voyage-4-large": 1024,
    "voyage-4": 1024,
    "voyage-4-lite": 1024,
    "voyage-law-2": 1024,
}

#: Per-request token budgets, derived from the API caps (120K law-2/4-large,
#: 320K voyage-4, 1M 4-lite) with ~8% headroom: counts are exact local
#: tokenizations (see :meth:`VoyageEmbedder._count_tokens`), the headroom only
#: covers any server-side counting drift (e.g. input_type prompt prepends).
_VOYAGE_TOKEN_BUDGETS = {
    "voyage-4-large": 110_000,
    "voyage-4": 295_000,
    "voyage-4-lite": 920_000,
    "voyage-law-2": 110_000,
}
#: Fallback for unknown voyage models: the most restrictive cap, with margin.
_DEFAULT_TOKEN_BUDGET = 110_000


def _estimate_tokens(text: str) -> int:
    """Fallback token estimate, used only when the local tokenizer is
    unavailable (offline, HF Hub down — see :meth:`VoyageEmbedder._count_tokens`).

    Italian legal text measured ~2.26 chars/token under Voyage's tokenizer
    (the earlier ``len // 3`` heuristic underestimated and blew the API cap),
    so ``len // 2`` overestimates and keeps batches safely under budget.
    """
    return max(1, len(text) // 2)


@runtime_checkable
class Embedder(Protocol):
    """Common interface for dense embedding providers."""

    name: str
    dim: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


#: Token window for bge-m3 encoding. The chunker caps every chunk at
#: ``legger.corpus.chunker.MAX_TEXT`` (8000) chars, and Italian legal text
#: measures ~2.26 chars/token (see the Voyage token-counting notes above), so
#: the worst chunk is ~3600 tokens — 4096 truncates NOTHING for our data.
#: bge-m3's native 8192 window is quadratically slower (attention) on CPU and
#: was the cause of multi-tens-of-minutes lots on long-sequence batches.
#: tests/test_embedders.py asserts MAX_TEXT / 2.0 < BGE_MAX_LENGTH so the
#: suite fails if the chunk cap ever outgrows this window.
BGE_MAX_LENGTH = 4096

#: Devices accepted by the LEGGER_EMBED_DEVICE override.
_VALID_DEVICES = ("cpu", "mps", "cuda")


def _detect_device() -> str:
    """Pick the torch device: ``LEGGER_EMBED_DEVICE`` override, else auto-detect.

    The env var (cpu|mps|cuda) is an operational escape hatch — e.g. MPS
    inference has wedged indefinitely at 0% CPU in detached (nohup) indexing
    runs on Intel Macs, where forcing ``LEGGER_EMBED_DEVICE=cpu`` is the
    reliable workaround. It is deliberately read from ``os.environ`` at detect
    time rather than through :class:`~legger.settings.Settings`, so operators
    can flip it per-process without touching configuration. An invalid value
    logs a warning and falls back to auto-detection (cuda > mps > cpu).
    """
    override = os.environ.get("LEGGER_EMBED_DEVICE")
    if override:
        if override in _VALID_DEVICES:
            return override
        logger.warning(
            "Ignoring invalid LEGGER_EMBED_DEVICE=%r (expected one of %s); "
            "auto-detecting device instead.",
            override,
            "|".join(_VALID_DEVICES),
        )

    import torch

    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


class BgeM3Embedder:
    """Local BAAI/bge-m3 via FlagEmbedding, dense vectors only.

    The ~2.2GB model is downloaded/loaded lazily on the first embed call, so
    importing this module (and constructing the embedder) stays cheap.
    Embeddings are L2-normalized by the model, so dot product == cosine.
    """

    MODEL_ID = "BAAI/bge-m3"

    name = "bge-m3"
    dim = 1024

    def __init__(self, batch_size: int = 32, device: str | None = None) -> None:
        self.batch_size = batch_size
        self._device = device
        self._model: Any = None

    def _get_model(self) -> Any:
        if self._model is None:
            from FlagEmbedding import BGEM3FlagModel

            device = self._device or _detect_device()
            self._device = device
            self._model = BGEM3FlagModel(
                self.MODEL_ID,
                devices=device,
                normalize_embeddings=True,
                use_fp16=device != "cpu",  # fp16 is slower than fp32 on CPU
            )
        return self._model

    def _encode(self, texts: list[str]) -> list[list[float]]:
        out = self._get_model().encode(
            texts,
            batch_size=self.batch_size,
            max_length=BGE_MAX_LENGTH,  # lossless for chunker-capped text; see constant
            return_dense=True,
            return_sparse=False,
            return_colbert_vecs=False,
        )
        return [vec.tolist() for vec in out["dense_vecs"]]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return self._encode(texts)

    def embed_query(self, text: str) -> list[float]:
        # bge-m3 is symmetric: queries and documents share the same encoding.
        return self._encode([text])[0]


class VoyageEmbedder:
    """Voyage AI API embedder with document/query input types.

    The API key is validated at construction (fail fast, before any long
    indexing run); the network client itself is created lazily. Retries on
    rate limits use the SDK's built-in exponential backoff (tenacity).

    Caller contract: ``embed_documents`` makes one API call per internal
    batch but returns nothing until ALL batches succeed — a failed call
    discards every batch within that call. Callers indexing large corpora
    must therefore embed in upsert-sized slices (so a failure loses only the
    current slice, and the run stays resumable by re-upserting that slice).

    Batching is token-aware: a batch is flushed when it reaches
    ``batch_size`` texts OR when adding the next text would exceed
    ``max_tokens_per_batch`` — the API enforces a per-request total-token cap
    on top of the text-count cap. Token counts are EXACT, computed locally
    with the model's own HF tokenizer (the same one the SDK's
    ``count_tokens`` uses), once per ``embed_documents`` call; if the
    tokenizer cannot be loaded, batching degrades to the conservative
    :func:`_estimate_tokens` with a logged warning, never crashing. A single
    text whose count alone exceeds the budget is sent alone: Voyage truncates
    over-context inputs server-side, so it cannot be split here without
    changing the embedding semantics.
    """

    def __init__(
        self,
        model: str = DEFAULT_VOYAGE_MODEL,
        *,
        api_key: str | None = None,
        batch_size: int = 128,
        max_tokens_per_batch: int | None = None,
        max_retries: int = 5,
    ) -> None:
        key = api_key if api_key is not None else Settings().voyage_api_key
        if not key:
            raise ValueError(
                "Voyage API key is not configured: set VOYAGE_API_KEY in the repo-root "
                ".env file (or pass api_key=) before using VoyageEmbedder."
            )
        self.name = model
        self.dim = _VOYAGE_DIMS.get(model, 1024)
        self.batch_size = batch_size
        self.max_tokens_per_batch = (
            max_tokens_per_batch
            if max_tokens_per_batch is not None
            else _VOYAGE_TOKEN_BUDGETS.get(model, _DEFAULT_TOKEN_BUDGET)
        )
        self._api_key = key
        self._max_retries = max_retries
        self._client: voyageai.Client | None = None
        self._tokenizer: Any = None
        self._tokenizer_failed = False

    def _get_client(self) -> voyageai.Client:
        if self._client is None:
            import voyageai

            self._client = voyageai.Client(api_key=self._api_key, max_retries=self._max_retries)
        return self._client

    def _get_tokenizer(self) -> Any | None:
        """Lazily load the model's HF tokenizer for exact local token counts.

        Same source the voyageai SDK's ``count_tokens`` uses
        (``voyageai/<model>`` on the HF Hub; downloaded once, then served from
        the local HF cache). Returns ``None`` — permanently, per instance — if
        loading fails (offline, HF down, unknown model), so batching can fall
        back to an estimate instead of crashing an indexing run.
        """
        if self._tokenizer is None and not self._tokenizer_failed:
            try:
                from tokenizers import Tokenizer

                tokenizer = Tokenizer.from_pretrained(f"voyageai/{self.name}")
                tokenizer.no_truncation()
                self._tokenizer = tokenizer
            except Exception:
                self._tokenizer_failed = True
                logger.warning(
                    "Could not load HF tokenizer voyageai/%s; falling back to "
                    "len//2 token estimates for batch packing.",
                    self.name,
                    exc_info=True,
                )
        return self._tokenizer

    def _count_tokens(self, texts: list[str]) -> list[int]:
        """Exact per-text token counts via the local tokenizer; estimates on
        fallback (see :meth:`_get_tokenizer`)."""
        tokenizer = self._get_tokenizer()
        if tokenizer is None:
            return [_estimate_tokens(text) for text in texts]
        return [len(encoding) for encoding in tokenizer.encode_batch(texts)]

    def _batches(self, texts: list[str]) -> Iterator[list[str]]:
        """Greedy split honoring both the text-count and the token budget.

        Tokens are counted once per call (one ``encode_batch`` over all
        texts). An empty batch always accepts the next text, so a single
        over-budget text goes out alone (see the class docstring on
        server-side truncation). A single input short-circuits — one text is
        always one batch — keeping the query path tokenizer-free.
        """
        if len(texts) <= 1:
            if texts:
                yield list(texts)
            return
        batch: list[str] = []
        batch_tokens = 0
        for text, tokens in zip(texts, self._count_tokens(texts), strict=True):
            if batch and (
                len(batch) >= self.batch_size or batch_tokens + tokens > self.max_tokens_per_batch
            ):
                yield batch
                batch, batch_tokens = [], 0
            batch.append(text)
            batch_tokens += tokens
        if batch:
            yield batch

    def _embed(self, texts: list[str], input_type: str) -> list[list[float]]:
        client = self._get_client()
        vectors: list[list[float]] = []
        for batch in self._batches(texts):
            result = client.embed(batch, model=self.name, input_type=input_type)
            vectors.extend(result.embeddings)
        return vectors

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return self._embed(texts, input_type="document")

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text], input_type="query")[0]


def get_embedder(name: str, **kwargs: Any) -> Embedder:
    """Build an embedder by name: ``"bge-m3"`` or any ``"voyage-*"`` model id."""
    if name == "bge-m3":
        return BgeM3Embedder(**kwargs)
    if name.startswith("voyage-"):
        return VoyageEmbedder(model=name, **kwargs)
    raise ValueError(f"Unknown embedder {name!r}; expected 'bge-m3' or a 'voyage-*' model id.")
