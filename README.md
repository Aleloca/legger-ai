# legger.ai

**RAG assistant over Italian legislation: semantic search and cited answers across the legal corpus, with a FastAPI/Qdrant backend and a Next.js frontend.**

**🔗 Live demo: [http://178.105.229.42](http://178.105.229.42)** — rate-limited per user (see [Demo & rate limiting](#demo--rate-limiting)).

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
```

### 4. Build the vector index

This is the step that produces the searchable knowledge base: it walks the
corpus, parses and chunks each act, embeds the chunks, and upserts them
(dense **and** BM25 sparse vectors) into a single Qdrant collection. Postgres
tracks per-file checkpoints, so the run is fully **resumable** — re-run the
same command after a crash or interruption and it picks up where it left off.

```sh
# Estimate scale/cost first — no API key, no Qdrant, no DB writes:
uv run legger ingest bootstrap --dry-run

# Build the whole index (creates the Qdrant collection if missing):
uv run legger ingest bootstrap --embedder voyage-4-large --qdrant-collection norme

# ...or index a subset of collections to try things out quickly:
uv run legger ingest bootstrap --collections "Codici,DPR" --qdrant-collection norme
```

> **Scale & cost.** The full corpus is large (hundreds of thousands of acts),
> so a complete bootstrap with `voyage-4-large` takes time and incurs **Voyage
> API usage costs**. Run `--dry-run` first to see the token estimate. For a
> fully local, no-API-key alternative, use `--embedder bge-m3` (a smaller,
> CPU/GPU-local dense model); the BM25 sparse side is always computed locally.

> **Embedder ↔ collection pairing.** The query-time embedder **must** match the
> one used to build the collection (same model ⇒ same vector space). When you
> serve the API, set `QDRANT_COLLECTION` and `EMBEDDER_NAME` in `.env` to the
> pair you indexed with (e.g. `QDRANT_COLLECTION=norme`,
> `EMBEDDER_NAME=voyage-4-large`).

Keeping the index up to date with the upstream corpus (git pull + diff, only
re-embedding what changed):

```sh
uv run legger ingest delta --embedder voyage-4-large --qdrant-collection norme
```

#### Snapshot: back up and reuse the built index

Once the collection is built you can export it as a **Qdrant snapshot** — a
single portable file — so you can back it up or restore it on another machine
**without re-running the bootstrap** (and without paying for embeddings again).
The snapshot contains the dense + BM25 vectors *and* the payload (chunk text),
so treat it like the corpus content when deciding whether to share it.

```sh
# 1. Create a snapshot of the `norme` collection (Qdrant on :6333):
curl -X POST "http://localhost:6333/collections/norme/snapshots?wait=true"

# 2. Copy it out of the Qdrant container (container name from `docker compose ps`):
docker cp <qdrant-container>:/qdrant/snapshots/norme/<SNAPSHOT>.snapshot ./norme.snapshot
shasum -a 256 ./norme.snapshot          # note the hash to verify after transfer

# 3. On the target machine, copy the file into its Qdrant container and recover:
docker cp ./norme.snapshot <qdrant-container>:/qdrant/snapshots/
curl -X PUT "http://localhost:6333/collections/norme/snapshots/recover?wait=true" \
  -H "Content-Type: application/json" \
  -d '{"location": "file:///qdrant/snapshots/norme.snapshot"}'

# 4. Verify: points_count should match and status should be green:
curl "http://localhost:6333/collections/norme"
```

> The snapshot for the full corpus is several GB. After recovery, set
> `QDRANT_COLLECTION`/`EMBEDDER_NAME` to the same pair the snapshot was built
> with, exactly as you would after a fresh bootstrap.

### 5. Run the backend and the CLI

```sh
uv run uvicorn legger.api.app:app --reload --port 8000   # API on :8000

uv run legger eval --collection norme --embedder voyage-4-large --rerank  # retrieval eval
uv run legger chat --collection norme --embedder voyage-4-large           # terminal chat
uv run legger --help                                                      # full reference
```

### 6. Frontend

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
Qdrant, Postgres, Redis (rate-limit counters), and a Caddy reverse proxy
(automatic HTTPS, HTTP/3). The full, step-by-step production runbook lives in
[`docs/deploy.md`](docs/deploy.md).

## Demo & rate limiting

A live demo is hosted at **[http://178.105.229.42](http://178.105.229.42)**.

`POST /chat` is the only endpoint that calls a paid LLM, so a public demo can
run up real cost. The backend ships an optional **per-user rate limiter**
(Redis-backed) to bound it. It is **off by default** — local development is
never throttled — and enabled in production via env.

Two independent limits, each enforced **per user** where a "user" is the pair
*(client IP, anonymous `lid` cookie)* — the stricter of the two identities
wins, so clearing cookies (IP still counts) or sharing a NAT (cookie still
counts) does not bypass it:

- **Concurrency** — at most `RATE_LIMIT_PER_USER_CONCURRENT` simultaneous
  `/chat` streams per user (blocks double-clicks / many open tabs).
- **Daily** — at most `RATE_LIMIT_PER_USER_DAILY` requests per calendar day
  (in `RATE_LIMIT_TZ`), reset at midnight.

When a limit is hit the request is refused **before** any tokens are spent,
with HTTP `429`, a `Retry-After` header, and a JSON body `{"code", "message"}`
(`code` is `daily_limit` | `concurrency_limit` | `unavailable`); the frontend
shows a localized message. If Redis is unreachable the limiter **fails closed**
(`429`) rather than letting unbounded traffic through.

| Env var | Default | Meaning |
| --- | --- | --- |
| `RATE_LIMIT_ENABLED` | `false` | Master switch (set `true` in prod) |
| `REDIS_URL` | `redis://localhost:6379` | Redis connection (prod: `redis://redis:6379`) |
| `RATE_LIMIT_PER_USER_CONCURRENT` | `2` | Max simultaneous `/chat` streams per user |
| `RATE_LIMIT_PER_USER_DAILY` | `30` | Max `/chat` requests per user per day |
| `RATE_LIMIT_TZ` | `Europe/Rome` | Timezone of the daily reset |

> Note: the limiter is per-user only — there is no global daily ceiling, so the
> theoretical maximum daily spend scales with the number of distinct users. See
> [`docs/plans/2026-06-16-rate-limiting-design.md`](docs/plans/2026-06-16-rate-limiting-design.md)
> for the full design and rationale.

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
