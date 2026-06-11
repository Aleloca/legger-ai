# legger API image (Task H1).
#
# Multi-stage: uv installs the locked dependency set into /app/.venv, then a
# plain python:3.12-slim runtime copies the ready-made venv (no uv, no build
# tools at runtime). The same image serves three roles in
# docker-compose.prod.yml:
#
#   api      uvicorn legger.api.app:app          (default CMD)
#   migrate  alembic upgrade head                (one-shot, compose `run`)
#   ingest   legger ingest delta                 (one-shot, compose `run`)
#
# SIZE NOTE: the locked dependency tree still includes FlagEmbedding/torch
# (linux resolves torch 2.12 + the full CUDA wheel stack), even though the
# production embedder is the voyage API and reranking is off. Their imports
# are lazy (legger/retrieval/embedders.py), so they are dead weight (~10 GB
# installed) — accepted for H1, trimming is an explicit follow-up.

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

# Toolchain for the few sdist-only deps (e.g. zlib-state, an ir-datasets/
# flagembedding transitive, ships no linux-aarch64 wheel). Builder-only.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# Dependency layer first so code edits don't re-resolve/re-download deps.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

# Project layer: the package itself plus the migration tree (alembic runs
# from /app at deploy: `alembic upgrade head` finds ./alembic.ini).
COPY legger ./legger
COPY alembic ./alembic
COPY alembic.ini ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

FROM python:3.12-slim-bookworm AS runtime

# git: corpus operations (`legger ingest delta` does git pull in /corpus).
# curl: the HEALTHCHECK below.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git curl \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 legger

COPY --from=builder --chown=legger:legger /app /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

USER legger
WORKDIR /app

EXPOSE 8000

# /healthz is dependency-free (legger/api/app.py): liveness only.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/healthz || exit 1

CMD ["uvicorn", "legger.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
