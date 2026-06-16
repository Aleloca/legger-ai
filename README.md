# legger.ai

**RAG assistant over Italian legislation: semantic search and cited answers across the legal corpus, with a FastAPI/Qdrant backend and a Next.js frontend.**

legger.ai indexes the Italian normative corpus (codes, laws, legislative and
presidential decrees, EU transposition acts, and more) into a hybrid search
index and answers natural-language questions with grounded, source-cited
responses. Every answer is backed by retrieved passages linked to the exact
act, article, and comma they come from.

> ⚠️ **Work in progress.** This is a research/production project under active
> development. The legal corpus itself is **not** included in this repository.

---

## How it works

```
              ┌────────────┐     ┌──────────────────────────┐
  Question ──▶│ Next.js UI │────▶│ FastAPI  /chat (SSE)      │
              └────────────┘     │  1. query understanding  │──▶ Claude
                                 │  2. hybrid retrieval      │──▶ Qdrant
                                 │  3. (optional) rerank     │──▶ cross-encoder
                                 │  4. grounded generation   │──▶ Claude
                                 └──────────────────────────┘
                                              │
                                  cited answer (streamed)
```

- **Hybrid retrieval** — dense vectors (Voyage `voyage-4-large` or `bge-m3`)
  combined with Qdrant server-side **BM25** sparse vectors, in a single Qdrant
  collection per embedder.
- **Optional cross-encoder reranking** (50 → k), toggled by `RERANK_ENABLED`.
- **Grounded generation** with Anthropic Claude: a query-understanding step, a
  guardrail, and streamed (SSE) answers that cite the retrieved acts.
- **Citations** resolved down to act / article / comma, linkable to the
  original normative text.
- **Feedback** (👍/👎) persisted to Postgres for retrieval quality analysis.

## Tech stack

| Layer        | Stack                                                          |
| ------------ | -------------------------------------------------------------- |
| Backend      | Python 3.12, FastAPI, Uvicorn, Pydantic, SQLAlchemy + Alembic  |
| Retrieval    | Qdrant (dense + BM25 sparse), fastembed, FlagEmbedding, Voyage |
| Generation   | Anthropic Claude                                               |
| Data store   | PostgreSQL 17                                                  |
| Frontend     | Next.js 16, React 19, Tailwind CSS 4, shadcn/ui                |
| Ops          | Docker Compose, Caddy (HTTP/3, automatic TLS)                  |

## Repository layout

```
backend/        FastAPI app, retrieval pipeline, ingestion, CLI, tests
  legger/
    api/        FastAPI routers: /chat, /search, /acts, /feedback
    retrieval/  embedders, hybrid index, rerank, citations, pipeline
    chat/       query understanding, prompts, guardrail, streaming
    ingestion/  corpus bootstrap + delta ingestion
    cli.py      the `legger` CLI (index, eval, ingest, feedback, chat)
frontend/       Next.js app
docs/           corpus analysis, deploy runbook, plans
docker-compose.yml        local infra (Qdrant + Postgres)
docker-compose.prod.yml   production stack (+ backend, frontend, Caddy)
```

## API

| Method & path        | Description                                            |
| -------------------- | ----------------------------------------------------- |
| `POST /chat`         | Grounded chat over the corpus (Server-Sent Events)    |
| `GET  /chat/models`  | Available generation models                            |
| `GET  /search`       | Raw hybrid retrieval results                            |
| `GET  /acts/{ref}`   | Resolve a single act by reference                      |
| `POST /feedback`     | Submit 👍/👎 feedback on a message                     |

---

## Getting started (local development)

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) and Docker Compose
- [uv](https://github.com/astral-sh/uv) (Python package/runtime manager)
- [Node.js](https://nodejs.org/) 20+ and npm
- An [Anthropic API key](https://console.anthropic.com/) and a
  [Voyage AI API key](https://www.voyageai.com/)
- A local clone of the Italian corpus next to this repo (see
  [The corpus](#the-corpus))

### 1. Configuration

```sh
cp .env.example .env
# then fill in ANTHROPIC_API_KEY and VOYAGE_API_KEY
```

### 2. Start the infrastructure

```sh
docker compose up -d        # Qdrant on :6333, Postgres on :5432
```

### 3. Backend

```sh
cd backend
uv sync                                   # install dependencies
uv run alembic upgrade head               # apply DB migrations
uv run uvicorn legger.api.app:app --reload --port 8000
```

The `legger` CLI handles indexing, evaluation, ingestion, and a terminal chat:

```sh
uv run legger index --collection "Codici" --embedder voyage-4-large
uv run legger eval  --collection norme --embedder voyage-4-large --rerank
uv run legger chat                        # interactive grounded chat
uv run legger --help                      # full command reference
```

### 4. Frontend

```sh
cd frontend
npm install
npm run dev                               # http://localhost:3000
```

## The corpus

legger.ai is designed to run against
[`italia-corpus`](https://github.com/ahmeabd/italia-corpus), a separate
repository of the Italian normative texts as Markdown, organised into
collections (Codici, DL e leggi di conversione, Decreti Legislativi, DPR, Regi
decreti, EU transposition acts, and others). The corpus is **not** bundled
here; point `CORPUS_PATH` in your `.env` at a local clone (default:
`../italia-corpus`). See [`docs/corpus-analysis.md`](docs/corpus-analysis.md)
for a breakdown.

## Deployment

A production stack (`docker-compose.prod.yml`) runs the backend, frontend,
Qdrant, Postgres, and a Caddy reverse proxy (automatic HTTPS, HTTP/3). The
full, step-by-step production runbook lives in
[`docs/deploy.md`](docs/deploy.md).

## Testing

```sh
cd backend  && uv run pytest        # backend tests
cd frontend && npm test             # frontend tests (vitest)
```

## Acknowledgements

This project would not exist without the
[**italia-corpus**](https://github.com/ahmeabd/italia-corpus) repository, which
provides the structured collection of Italian normative texts that legger.ai
indexes and reasons over. Heartfelt thanks to its maintainer,
[**@ahmeabd**](https://github.com/ahmeabd), for assembling and sharing this
material — it is the foundation the entire project is built upon.

## License

Released under the [MIT License](LICENSE). © 2026 Alessandro Locatelli.
