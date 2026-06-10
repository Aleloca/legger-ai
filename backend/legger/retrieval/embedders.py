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
and the CPU-only VPS. Measured on this machine (MPS): ~9 docs/s at 1.5k chars,
i.e. ~33 min for the 18.5k Codici chunks.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from legger.settings import Settings

if TYPE_CHECKING:
    from collections.abc import Iterator

    import voyageai

DEFAULT_VOYAGE_MODEL = "voyage-law-2"

#: Output dimensions per Voyage model (all current models default to 1024).
_VOYAGE_DIMS = {
    "voyage-4-large": 1024,
    "voyage-4": 1024,
    "voyage-4-lite": 1024,
    "voyage-law-2": 1024,
}

#: Per-request token budgets, derived from the API caps (120K law-2/4-large,
#: 320K voyage-4, 1M 4-lite) with margin: token counts are estimated locally
#: (see :func:`_estimate_tokens`), so the budget stays well under the hard cap.
_VOYAGE_TOKEN_BUDGETS = {
    "voyage-4-large": 100_000,
    "voyage-4": 280_000,
    "voyage-4-lite": 900_000,
    "voyage-law-2": 100_000,
}
#: Fallback for unknown voyage models: the most restrictive cap, with margin.
_DEFAULT_TOKEN_BUDGET = 100_000


def _estimate_tokens(text: str) -> int:
    """Conservative local token estimate for batching (no API round-trips).

    Italian legal text runs ~3.5-4 chars/token under Voyage's tokenizer, so
    ``len // 3`` safely overestimates. The voyageai client does expose
    ``count_tokens``, but calling it per text would cost an API round-trip
    per chunk — not worth it for a batching heuristic.
    """
    return max(1, len(text) // 3)


@runtime_checkable
class Embedder(Protocol):
    """Common interface for dense embedding providers."""

    name: str
    dim: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


def _detect_device() -> str:
    """Pick the best available torch device: cuda > mps > cpu."""
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
            max_length=8192,  # bge-m3's native limit; padding is to batch-longest
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
    ``max_tokens_per_batch`` (estimated locally, see :func:`_estimate_tokens`)
    — the API enforces a per-request total-token cap on top of the text-count
    cap. A single text whose estimate alone exceeds the budget is sent alone:
    Voyage truncates over-context inputs server-side, so it cannot be split
    here without changing the embedding semantics.
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

    def _get_client(self) -> voyageai.Client:
        if self._client is None:
            import voyageai

            self._client = voyageai.Client(api_key=self._api_key, max_retries=self._max_retries)
        return self._client

    def _batches(self, texts: list[str]) -> Iterator[list[str]]:
        """Greedy split honoring both the text-count and the token budget.

        An empty batch always accepts the next text, so a single over-budget
        text goes out alone (see the class docstring on server-side truncation).
        """
        batch: list[str] = []
        batch_tokens = 0
        for text in texts:
            tokens = _estimate_tokens(text)
            if batch and (
                len(batch) >= self.batch_size
                or batch_tokens + tokens > self.max_tokens_per_batch
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
