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
retrieval-specific prompts); max 1,000 texts per request. The voyageai SDK
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
    import voyageai

DEFAULT_VOYAGE_MODEL = "voyage-law-2"

#: Output dimensions per Voyage model (all current models default to 1024).
_VOYAGE_DIMS = {
    "voyage-4-large": 1024,
    "voyage-4": 1024,
    "voyage-4-lite": 1024,
    "voyage-law-2": 1024,
}


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
    """

    def __init__(
        self,
        model: str = DEFAULT_VOYAGE_MODEL,
        *,
        api_key: str | None = None,
        batch_size: int = 128,
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
        self._api_key = key
        self._max_retries = max_retries
        self._client: voyageai.Client | None = None

    def _get_client(self) -> voyageai.Client:
        if self._client is None:
            import voyageai

            self._client = voyageai.Client(api_key=self._api_key, max_retries=self._max_retries)
        return self._client

    def _embed(self, texts: list[str], input_type: str) -> list[list[float]]:
        client = self._get_client()
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
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
