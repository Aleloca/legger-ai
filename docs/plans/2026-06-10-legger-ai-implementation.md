# Legger.ai — Implementation Plan (Ciclo 1: Spike + Core Pipeline + UI Chat)

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Costruire e deployare la prima versione funzionante di legger.ai: pipeline RAG sulla legislazione italiana (corpus italia-corpus) con chat in streaming, citazioni verificate e split-view della norma.

**Architecture:** Servizio Python FastAPI (parsing, ingestion, hybrid retrieval su Qdrant, generation con Claude, SSE) + frontend Next.js puro. Monorepo `backend/` + `frontend/`, Postgres per i metadata, Qdrant per i vettori, clone git del corpus come mirror e database storico. Design completo: `docs/plans/2026-06-10-legger-ai-design.md`.

**Tech Stack:** Python 3.12 + uv, FastAPI, pydantic v2, qdrant-client, fastembed (BM25 sparse), sentence-transformers/FlagEmbedding (bge-m3), anthropic SDK, SQLAlchemy 2 + Alembic + psycopg, pytest, ruff. Frontend: Next.js 15 App Router, TypeScript, Tailwind, shadcn/ui. Deploy: Docker Compose + Caddy su Hetzner.

**Vincoli trasversali:**
- TDD su tutto il codice deterministico (parser, chunker, regex, guardrail). La qualità del retrieval si misura con `legger eval`, non si unit-testa.
- Commit frequenti, uno per task o sotto-task completato.
- Ogni chiamata a modelli Anthropic: verificare id modello e prezzi correnti con la skill `claude-api` prima di scrivere il codice.
- ⚠️ **CHECKPOINT GO/NO-GO alla fine della Fase C**: non si procede con la Fase D se recall@10 < 85% sul set di valutazione.

---

## Fase A — Setup del monorepo

### Task A1: Scaffold backend Python

**Files:**
- Create: `backend/pyproject.toml`, `backend/legger/__init__.py`, `backend/legger/corpus/__init__.py`, `backend/legger/ingestion/__init__.py`, `backend/legger/retrieval/__init__.py`, `backend/legger/chat/__init__.py`, `backend/legger/api/__init__.py`, `backend/tests/__init__.py`, `backend/.python-version`

**Step 1:** Creare il progetto con uv:

```bash
cd backend && uv init --name legger --python 3.12
uv add fastapi uvicorn[standard] pydantic pydantic-settings qdrant-client fastembed anthropic sqlalchemy psycopg[binary] alembic httpx
uv add --dev pytest pytest-asyncio ruff
```

**Step 2:** In `pyproject.toml` aggiungere entrypoint CLI e config tool:

```toml
[project.scripts]
legger = "legger.cli:main"

[tool.ruff]
line-length = 100

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

**Step 3:** Creare `backend/legger/cli.py` minimale (argparse con sotto-comandi vuoti `ingest`, `eval`, `chat`) e i package `__init__.py` vuoti.

**Step 4:** Verifica: `uv run legger --help` stampa i sotto-comandi; `uv run pytest` esce verde (0 test); `uv run ruff check .` pulito.

**Step 5:** Commit: `chore: scaffold backend python project`

### Task A2: Docker Compose dev (Qdrant + Postgres)

**Files:**
- Create: `docker-compose.yml`, `.env.example`, `backend/legger/settings.py`

**Step 1:** `docker-compose.yml` con `qdrant/qdrant:latest` (porta 6333, volume `qdrant_data`) e `postgres:17` (porta 5432, volume `pg_data`, db/user/password `legger` da env).

**Step 2:** `.env.example` con: `DATABASE_URL=postgresql+psycopg://legger:legger@localhost:5432/legger`, `QDRANT_URL=http://localhost:6333`, `ANTHROPIC_API_KEY=`, `VOYAGE_API_KEY=`, `CORPUS_PATH=../italia-corpus`.

**Step 3:** `backend/legger/settings.py` con `pydantic_settings.BaseSettings` che legge le stesse chiavi.

**Step 4:** Verifica: `docker compose up -d` → `curl localhost:6333/readyz` ok, `psql` connette.

**Step 5:** Commit: `chore: add dev docker-compose (qdrant + postgres) and settings`

---

## Fase B — Spike: corpus, parser, chunker

### Task B1: Clone del corpus e script di analisi

**Files:**
- Create: `backend/scripts/analyze_corpus.py`
- Create (output): `docs/corpus-analysis.md`

**Step 1:** Clonare il corpus FUORI dal repo (è multi-GB): `git clone https://github.com/ahmeabd/italia-corpus ../italia-corpus` (path = `CORPUS_PATH`).

**Step 2:** Scrivere `analyze_corpus.py` che produce su stdout (in Markdown):
- elenco delle 23 collezioni con conteggio file e dimensione totale;
- per la collezione `Codici`: distribuzione lunghezze file, primi 50 caratteri di 10 file campione;
- censimento dei pattern di intestazione (`^#{1,6} ` per livello) su un campione di 500 file per collezione: quali livelli esistono, come sono marcati articoli/libri/titoli/capi, c'è frontmatter YAML?;
- naming convention dei file (da cui derivare `act_ref`);
- nomi esatti delle cartelle "abrogati"/"decaduti" (per la mappa di vigenza);
- casi patologici: 10 file più lunghi, 10 più corti, file senza intestazioni.

**Step 3:** Eseguire e salvare l'output in `docs/corpus-analysis.md`. **Leggere il report e fissare per iscritto, in fondo al report, le assunzioni del parser** (es. "articolo = heading di livello N con pattern `Art. X`"). ⚠️ I task B3–B4 vanno adattati a queste assunzioni reali: i pattern nel piano sono ipotesi da verificare.

**Step 4:** Commit: `docs: corpus structure analysis report`

### Task B2: Fixture reali per i test

**Files:**
- Create: `backend/tests/fixtures/corpus/` (file copiati dal corpus reale)

**Step 1:** Copiare dal corpus 6–10 file rappresentativi scelti leggendo il report B1: un codice grande (es. codice civile o penale), un D.Lgs. medio, un atto brevissimo, un articolo-fiume (legge di bilancio), un atto da cartella abrogati, eventuali file anomali trovati. Mantenere i path relativi originali (servono per `act_ref` e vigenza).

**Step 2:** Commit: `test: add real corpus fixtures`

### Task B3: Parser dell'atto (TDD)

**Files:**
- Create: `backend/legger/corpus/models.py`, `backend/legger/corpus/parser.py`
- Test: `backend/tests/test_parser.py`

**Step 1:** Modelli pydantic:

```python
# legger/corpus/models.py
from typing import Literal
from pydantic import BaseModel

Vigenza = Literal["vigente", "abrogato", "decaduto"]

class Comma(BaseModel):
    number: str | None  # "1", "2-bis"; None se l'articolo non è scandito in commi
    text: str

class Article(BaseModel):
    number: str          # "18", "613-bis"
    heading: str | None  # rubrica
    path: list[str]      # es. ["Libro IV", "Titolo II", "Capo I"]
    commi: list[Comma]

class Act(BaseModel):
    act_ref: str
    act_type: str
    title: str | None
    collection: str
    vigenza: Vigenza
    file_path: str
    articles: list[Article]
```

**Step 2: Test falliti prima.** Per ogni fixture, test concreti con valori veri letti a mano dal file (adattare numeri e rubriche alle fixture reali):

```python
# tests/test_parser.py
from pathlib import Path
from legger.corpus.parser import parse_act

FIXTURES = Path(__file__).parent / "fixtures" / "corpus"

def test_parses_articles_from_dlgs():
    act = parse_act(FIXTURES / "Decreti Legislativi" / "<file-fixture>.md")
    art18 = next(a for a in act.articles if a.number == "18")
    assert art18.heading  # rubrica presente
    assert len(art18.commi) >= 1

def test_article_path_contains_hierarchy():
    act = parse_act(FIXTURES / "Codici" / "<codice-fixture>.md")
    art = next(a for a in act.articles if a.number == "2051")
    assert any("Libro" in p for p in art.path)

def test_tiny_act_single_article():
    act = parse_act(FIXTURES / "<collezione>" / "<atto-breve>.md")
    assert len(act.articles) >= 1  # mai zero articoli su file valido
```

**Step 3:** Run: `uv run pytest tests/test_parser.py -v` → FAIL (modulo inesistente).

**Step 4:** Implementare `parse_act(path, corpus_root) -> Act`: parsing line-based delle intestazioni Markdown secondo le assunzioni di B1; articoli riconosciuti dal pattern reale; commi scanditi per numerazione (`1.`, `2-bis.` …) quando presente, altrimenti un unico `Comma(number=None)`. Lo stack della gerarchia (libro/titolo/capo) si mantiene attraversando le intestazioni di livello superiore.

**Step 5:** Run: `uv run pytest tests/test_parser.py -v` → PASS.

**Step 6:** Commit: `feat: markdown act parser with article/comma hierarchy`

### Task B4: act_ref canonico e vigenza dal path (TDD)

**Files:**
- Create: `backend/legger/corpus/refs.py`
- Test: `backend/tests/test_refs.py`

**Step 1: Test falliti prima** (adattare ai naming reali da B1):

```python
from legger.corpus.refs import act_ref_from_path, vigenza_from_path

def test_act_ref_from_filename():
    assert act_ref_from_path("Decreti Legislativi/<nome-reale-dlgs-81-2008>.md") == "dlgs-81-2008"

def test_vigenza_from_abrogati_folder():
    assert vigenza_from_path("<nome reale cartella abrogati>/foo.md") == "abrogato"

def test_vigenza_default_vigente():
    assert vigenza_from_path("Codici/codice-civile.md") == "vigente"
```

**Step 2:** FAIL → implementare: mappa esplicita `cartella → (act_type, vigenza)` ricavata dal report B1 (la vigenza è curata upstream, non si inventa); slug `act_ref` deterministico da tipo+numero+anno.

**Step 3:** PASS → Commit: `feat: canonical act_ref and vigenza derivation from corpus paths`

### Task B5: Chunker (TDD)

**Files:**
- Create: `backend/legger/corpus/chunker.py`
- Test: `backend/tests/test_chunker.py`

**Step 1: Test falliti prima:**

```python
from legger.corpus.chunker import chunk_act

def test_one_chunk_per_normal_article(parsed_dlgs):
    chunks = chunk_act(parsed_dlgs)
    art18 = [c for c in chunks if c.article == "18"]
    assert len(art18) == 1

def test_header_prefixed_to_text(parsed_dlgs):
    c = next(c for c in chunk_act(parsed_dlgs) if c.article == "18")
    assert c.text.startswith(c.header)
    assert c.act_ref in c.header  # estremi nell'header contestuale

def test_long_article_split_with_overlap(parsed_bilancio):
    chunks = [c for c in chunk_act(parsed_bilancio) if c.article == "1"]
    assert len(chunks) > 1
    # overlap di 1 comma tra chunk consecutivi
    assert chunks[0].commi[-1] == chunks[1].commi[0]
    # header ripetuto con range commi
    assert "commi" in chunks[1].header

def test_chunk_ids_stable_and_unique(parsed_dlgs):
    ids = [c.id for c in chunk_act(parsed_dlgs)]
    assert len(ids) == len(set(ids))
    assert ids == [c.id for c in chunk_act(parsed_dlgs)]  # deterministico
```

**Step 2:** FAIL → implementare `chunk_act(act: Act) -> list[Chunk]`:
- 1 articolo = 1 chunk; soglia di split: > ~25 commi o > ~6000 caratteri → gruppi di commi con overlap 1;
- header contestuale: `"{tipo esteso} {estremi} — {titolo atto}\nArt. {n}{rubrica}{range commi se split}"`;
- `Chunk.id = f"{act_ref}#art-{number}#{i}"`; modello `Chunk` con i campi payload del design §4.2.

**Step 3:** PASS → Commit: `feat: article-based chunker with contextual headers`

---

## Fase C — Spike: benchmark embedding, indice, CLI, GO/NO-GO

### Task C1: Set di query di valutazione

**Files:**
- Create: `backend/eval/queries.yaml`

**Step 1:** Scrivere a mano ~30 query con risposta attesa, SOLO su norme presenti nella collezione `Codici` (il campione indicizzato). Mix obbligato: 10 lookup per estremi ("art. 2051 c.c."), 12 linguaggio naturale ("responsabilità per danno da cose in custodia"), 5 lessico atecnico da cittadino, 3 trabocchetti (articoli abrogati/numerazioni simili). Formato:

```yaml
- id: q01
  query: "responsabilità per i danni cagionati da cose in custodia"
  expected: { act_ref: codice-civile, article: "2051" }
  kind: natural
```

**Step 2:** Commit: `test: retrieval eval query set (30 queries on Codici)`

### Task C2: Interfaccia embedding con due provider

**Files:**
- Create: `backend/legger/retrieval/embedders.py`
- Test: `backend/tests/test_embedders.py` (solo shape/contratto, no qualità)

**Step 1:** Verificare con la skill `claude-api` / docs correnti i modelli embedding API disponibili (Voyage). `uv add FlagEmbedding voyageai`.

**Step 2:** Protocollo comune:

```python
class Embedder(Protocol):
    name: str
    dim: int
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...
```

Implementazioni: `BgeM3Embedder` (FlagEmbedding, device MPS su Mac, batch 32) e `VoyageEmbedder` (API, batch 128, retry su rate limit).

**Step 3:** Test di contratto (dim coerente, batch vuoto, testo lungo troncato senza eccezioni). PASS → Commit: `feat: embedding providers (bge-m3 local, voyage api)`

### Task C3: Indicizzazione del campione Codici su Qdrant

**Files:**
- Create: `backend/legger/retrieval/index.py`, comando `legger index --collection Codici --embedder bge-m3`

**Step 1:** `ensure_collection(client, name, dim)`: named vectors `dense` (cosine) + sparse `bm25` (fastembed `Qdrant/bm25`); indici payload su `vigenza`, `act_type`, `act_ref`.

**Step 2:** Pipeline: scandisci `Codici/` → parse → chunk → embed (batch, barra di progresso) → upsert a lotti di 256 con id deterministici (uuid5 da `Chunk.id`). Una collezione Qdrant per embedder in benchmark: `norme_bgem3`, `norme_voyage`.

**Step 3:** Eseguire per entrambi gli embedder. Annotare: n. chunk totali, tempo, costo API Voyage → servono al go/no-go (stima bootstrap).

**Step 4:** Commit: `feat: qdrant indexing pipeline for corpus sample`

### Task C4: Hybrid search + harness di valutazione

**Files:**
- Create: `backend/legger/retrieval/search.py`, `backend/legger/eval_retrieval.py`, comando `legger eval`

**Step 1:** `hybrid_search(query, collection, k=10)`: query dense + query sparse BM25, fusione RRF (Qdrant Query API `prefetch` + `fusion=rrf`), filtro `vigenza=vigente` di default.

**Step 2:** `legger eval --collection norme_bgem3`: per ogni query in `queries.yaml`, hit se un chunk nei top-k ha `act_ref` E `article` attesi. Output: tabella recall@5, recall@10, MRR, ed elenco delle query mancate (con i top-3 restituiti, per il debug).

**Step 3:** Eseguire su entrambe le collezioni. Salvare i risultati in `docs/corpus-analysis.md` (sezione "Benchmark embedding") e **decidere l'embedder** (questione aperta #3 del design).

**Step 4:** Commit: `feat: hybrid search with RRF and retrieval eval harness`

### Task C5: Chat CLI minimale grounded

**Files:**
- Create: `backend/legger/chat/prompts.py`, `backger/legger/chat/generate.py`, comando `legger chat`

**Step 1:** Verificare id modello corrente Sonnet con skill `claude-api`. System prompt in `prompts.py` con le regole ferree del design §4.4 + istruzione marker `[[act_ref|art.N|c.N]]`.

**Step 2:** REPL: input → hybrid_search top 10 → contesto formattato (un blocco per chunk con header) → Sonnet streaming → stdout. Provare a mano ~10 query del set e giudicare le risposte.

**Step 3:** Commit: `feat: minimal grounded chat CLI`

### ⚠️ Task C6: CHECKPOINT GO/NO-GO

Criteri (dal design §10): recall@10 ≥ 85% sull'embedder scelto · stima fondata di chunk totali e costo bootstrap · risposte CLI ancorate e sensate.
- **GO** → scrivere le conclusioni in `docs/corpus-analysis.md`, proseguire con Fase D.
- **NO-GO** → iterare su chunking/header/query-set (tornare a B5/C4), NON proseguire. Se dopo 2-3 iterazioni il recall non sale, fermarsi e ridiscutere il design con Alessandro.

---

## Fase D — Ingestion completa

### Task D1: Schema Postgres + Alembic

**Files:**
- Create: `backend/legger/db.py` (engine, tabelle SQLAlchemy core), `backend/alembic/` (init + prima migration)

**Step 1:** Tabelle: `acts` (act_ref PK, act_type, title, collection, vigenza, file_path, last_commit_sha, last_updated), `ingestion_runs` (id, started_at, finished_at, commit_from, commit_to, files_processed, errors jsonb, status), `ingestion_progress` (file_path PK, commit_sha, indexed_at).

**Step 2:** `alembic init`, configurare URL da settings, `alembic revision --autogenerate`, `alembic upgrade head`. Verifica con psql `\dt`.

**Step 3:** Commit: `feat: postgres schema for acts and ingestion tracking`

### Task D2: Bootstrap con checkpoint/resume (TDD sulla logica di resume)

**Files:**
- Create: `backend/legger/ingestion/bootstrap.py`, comando `legger ingest bootstrap`
- Test: `backend/tests/test_bootstrap_resume.py`

**Step 1: Test prima** (con Postgres di test e corpus = fixtures): un run interrotto a metà (mock che alza eccezione al file N) riparte saltando i file già in `ingestion_progress`; un file modificato (sha diverso) viene re-processato.

**Step 2:** Implementare: scandisci tutto il corpus → per ogni file non in progress (o con sha cambiato): parse → chunk → embed → upsert Qdrant → upsert `acts` → segna progress. Batch embedding, log ogni 1000 file, gestione errori per-file (logga e continua, accumula in `ingestion_runs.errors`).

**Step 3:** PASS sui test → lanciare il bootstrap reale completo (ore: lasciarlo girare, è ripartibile). Annotare numeri finali in `docs/corpus-analysis.md`.

**Step 4:** Commit: `feat: full corpus bootstrap with checkpoint/resume`

### Task D3: Ingestion delta git-driven (TDD)

**Files:**
- Create: `backend/legger/ingestion/delta.py`, comando `legger ingest delta`
- Test: `backend/tests/test_delta.py`

**Step 1: Test prima**, su un repo git temporaneo creato dalla fixture (`tmp_path`): file aggiunto → indicizzato; modificato → re-indicizzato (vecchi punti dell'atto sostituiti); spostato in cartella abrogati → vigenza aggiornata su punti Qdrant (`set_payload`) e su `acts`, **punti non cancellati**; file eliminato → vigenza `abrogato`, punti conservati.

**Step 2:** Implementare: `git pull` → `git diff --name-status <last_commit>..HEAD` (last_commit da `ingestion_runs`, non `HEAD@{1}`: più robusto) → processa il delta → registra run.

**Step 3:** PASS → Commit: `feat: git-diff-driven delta ingestion preserving historical points`

### Task D4: Alerting

**Files:**
- Modify: `backend/legger/ingestion/delta.py`; Create: `backend/legger/alerts.py`

**Step 1:** `send_alert(msg)` via Telegram Bot API (token/chat_id da settings; no-op se assenti). Trigger: pull fallito, run con errori > soglia, ultimo commit upstream più vecchio di 7 giorni.

**Step 2:** Test con mock httpx. Commit: `feat: ingestion alerting via telegram`

---

## Fase E — Retrieval completo

### Task E1: Fast path per estremi espliciti (TDD pesante)

**Files:**
- Create: `backend/legger/retrieval/fastpath.py`
- Test: `backend/tests/test_fastpath.py`

**Step 1: Test prima** — tabella di ~25 casi:

```python
import pytest
from legger.retrieval.fastpath import extract_refs

@pytest.mark.parametrize("query,expected", [
    ("art. 2051 c.c.", [("codice-civile", "2051")]),
    ("articolo 613-bis del codice penale", [("codice-penale", "613-bis")]),
    ("d.lgs. 81/2008 art 18", [("dlgs-81-2008", "18")]),
    ("D.Lgs. n. 81 del 2008", [("dlgs-81-2008", None)]),
    ("DL 77/2021", [("dl-77-2021", None)]),
    ("l. 197/2022 art. 1", [("legge-197-2022", "1")]),
    ("posso licenziare in malattia?", []),          # nessun estremo
    ("ho 81 anni e nel 2008...", []),               # falso positivo da evitare
])
def test_extract_refs(query, expected):
    assert extract_refs(query) == expected
```

**Step 2:** FAIL → implementare con regex composte: abbreviazioni codici (`c.c.`, `c.p.`, `c.p.c.`, `c.p.p.`, `cost.` → act_ref noti), tipi atto (`d\.?\s?lgs`, `d\.?l\.`, `d\.?p\.?r\.?`, `l(egge)?\.`), numero/anno (`n. 81 del 2008`, `81/2008`), articoli (`art\.?\s*\d+(-\w+)?`). L'estremo è valido solo se tipo+numero+anno (o codice abbreviato) sono adiacenti.

**Step 3:** PASS → `resolve_refs(refs) -> list[Chunk]`: lookup su `acts` + fetch chunk da Qdrant per `act_ref`+`article` (scroll con filtro payload).

**Step 4:** Commit: `feat: fast path for explicit normative references`

### Task E2: Query understanding (Haiku, tool use)

**Files:**
- Create: `backend/legger/chat/understanding.py`
- Test: `backend/tests/test_understanding.py` (mockando l'API)

**Step 1:** Verificare id Haiku corrente con skill `claude-api`. Definire tool `analyze_query` con schema: `{rewritten_query: str, explicit_refs: [...], temporal_reference: str|null, wants_historical: bool}`. Input: ultimi N turni di conversazione + messaggio corrente (risolve "e il comma successivo?"). `tool_choice` forzato sul tool.

**Step 2:** Test con risposta API mockata: il parsing del tool_use produce il modello tipizzato; fallback su query letterale se la chiamata fallisce (la chat non deve mai rompersi per il QU).

**Step 3:** Commit: `feat: query understanding via haiku tool use`

### Task E3: Reranker + misura del delta

**Files:**
- Create: `backend/legger/retrieval/rerank.py`; Modify: `search.py`, `eval_retrieval.py`

**Step 1:** `uv add` il package del reranker; `rerank(query, chunks, top_k=10)` con `bge-reranker-v2-m3` (CPU, lazy load del modello).

**Step 2:** Estendere `legger eval --rerank`: confronto con/senza sul set di query. **Decisione registrata nel report:** se il delta di recall@10 è < 3 punti, il rerank resta disattivato di default (latenza in meno) — flag di config.

**Step 3:** Commit: `feat: optional cross-encoder reranking with measured impact`

### Task E4: Citation-following 1 hop (TDD su estrazione rinvii)

**Files:**
- Create: `backend/legger/retrieval/citations.py`
- Test: `backend/tests/test_citation_following.py`

**Step 1: Test prima:** da testi con "di cui all'articolo 14 del decreto legislativo 9 aprile 2008, n. 81" / "ai sensi dell'art. 1341 c.c." si estraggono i riferimenti (riusa `extract_refs` di E1 esteso ai pattern in prosa con date); rinvii interni allo stesso atto risolti su quell'atto.

**Step 2:** `follow_citations(chunks, token_budget=4000)`: estrai rinvii dai chunk top → fetch articoli richiamati (fast path) → appendi finché sotto budget (stima token: `len(text)//4`), dedup con i chunk già presenti.

**Step 3:** PASS → Commit: `feat: 1-hop citation following with token budget`

### Task E5: Pipeline di retrieval unificata

**Files:**
- Create: `backend/legger/retrieval/pipeline.py`

**Step 1:** `retrieve(conversation, message) -> RetrievalResult`: QU (E2) → fast path se estremi (E1) → altrimenti hybrid (C4) + rerank se attivo (E3) → citation-following (E4) → `RetrievalResult(chunks, sources, used_fastpath)`. Smoke test di integrazione (richiede servizi up, marcato `@pytest.mark.integration`).

**Step 2:** Aggiornare `legger chat` per usare la pipeline completa. Provare il set di query a mano.

**Step 3:** Commit: `feat: unified retrieval pipeline`

---

## Fase F — API FastAPI

### Task F1: App FastAPI + GET /acts/{act_ref}

**Files:**
- Create: `backend/legger/api/app.py`, `backend/legger/api/acts.py`
- Test: `backend/tests/test_api_acts.py` (TestClient, corpus = fixtures)

**Step 1: Test prima:** `GET /acts/dlgs-81-2008` → 200 con `{act_ref, title, vigenza, articles: [{number, heading, path, commi, html_anchor}]}`; act inesistente → 404.

**Step 2:** Implementare: lookup `acts` per file_path → `parse_act` (stesso modulo corpus, con cache LRU in-process) → serializzazione. CORS per il frontend dev.

**Step 3:** PASS → Commit: `feat: fastapi app with act detail endpoint`

### Task F2: POST /chat con SSE

**Files:**
- Create: `backend/legger/api/chat.py`, `backend/legger/chat/stream.py`

**Step 1:** Contratto SSE (documentarlo in docstring — è l'interfaccia col frontend):
- `event: status` `{"stage": "searching"}` → emesso subito;
- `event: sources` `{"sources": [{act_ref, article, title, vigenza}]}` → dopo il retrieval (tutte le fonti consultate);
- `event: token` `{"text": "..."}` → delta di testo dal modello;
- `event: citation` `{marker, act_ref, article, commi, title, vigenza, verified: true}` → quando un marker completo appare nello stream;
- `event: done` `{}` / `event: error` `{message}`.

**Step 2:** Body: `{messages: [{role, content}]}` (stateless). Implementare con `StreamingResponse`: pipeline retrieval → prompt con contesto → `anthropic` streaming → parser incrementale dei marker `[[...]]` sul flusso (bufferizza tra `[[` e `]]`, emette `citation` + il testo del marker come token). Prompt caching: `cache_control` su system prompt e blocco contesto.

**Step 3:** Test: TestClient streaming con client Anthropic mockato che emette testo con marker → si verificano sequenza e payload degli eventi.

**Step 4:** Commit: `feat: streaming chat endpoint with typed SSE events`

### Task F3: Guardrail citazioni (TDD)

**Files:**
- Create: `backend/legger/chat/guardrail.py`; Modify: `chat/stream.py`
- Test: `backend/tests/test_guardrail.py`

**Step 1: Test prima:** marker il cui `(act_ref, article)` è nei chunk del contesto → `verified=True`; marker orfano → `verified=False`; marker malformato → trattato come testo normale.

**Step 2:** Integrare nello stream: ogni `citation` esce già con `verified` valorizzato (evento `citation` vs design: un solo evento con flag, più semplice di due tipi). PASS → Commit: `feat: post-generation citation guardrail`

### Task F4: GET /search

**Files:**
- Create: `backend/legger/api/search.py` + test

**Step 1:** `GET /search?q=...` → fast path se estremi, altrimenti hybrid top 10 → `[{act_ref, article, title, snippet, vigenza}]`. Test con servizi mockati.

**Step 2:** Commit: `feat: direct search endpoint`

---

## Fase G — Frontend Next.js

### Task G1: Scaffold + proxy

**Files:**
- Create: `frontend/` (create-next-app TypeScript + Tailwind + App Router), config shadcn/ui

**Step 1:** `npx create-next-app@latest frontend --ts --tailwind --app --no-src-dir`; `npx shadcn@latest init`. Rewrite in `next.config.ts`: `/api/backend/:path*` → `http://localhost:8000/:path*`.

**Step 2:** Verifica `npm run dev` + fetch di `/api/backend/acts/...`. Commit: `chore: scaffold nextjs frontend with backend proxy`

### Task G2: Chat con streaming SSE

**Files:**
- Create: `frontend/app/page.tsx`, `frontend/components/chat.tsx`, `frontend/lib/sse.ts`, `frontend/lib/types.ts`

**Step 1:** `lib/types.ts`: tipi speculari al contratto SSE di F2. `lib/sse.ts`: client `fetch` + `ReadableStream` parser SSE (POST, quindi niente EventSource nativo) con callback per evento.

**Step 2:** `chat.tsx`: stato messaggi in React (conversazione solo client, design §3.1), textarea + invio, render dei token in arrivo, indicatore "sto cercando nel corpus…" su `status`, gestione `error`. Layout due colonne con pannello destro placeholder.

**Step 3:** Verifica manuale contro il backend reale. Commit: `feat: streaming chat ui`

### Task G3: Marker → chip citazione (TDD)

**Files:**
- Create: `frontend/lib/parse-markers.ts`, `frontend/components/citation-chip.tsx`
- Test: `frontend/lib/parse-markers.test.ts` (vitest: `npm i -D vitest`)

**Step 1: Test prima:** `parseMarkers("testo [[dlgs-81-2008|art.18|c.1]] altro")` → `[{text}, {citation:{actRef,article,comma}}, {text}]`; marker incompleto a fine stringa (streaming!) → tenuto in coda come pending, non renderizzato a metà; marker malformato → testo letterale.

**Step 2:** FAIL → implementare → PASS. `citation-chip.tsx`: chip con label umanizzata ("D.Lgs. 81/2008, art. 18, c. 1"), badge vigenza (verde/grigio) e stato ambra se `verified=false` (dall'evento `citation` correlato via marker). Render della risposta: react-markdown sui segmenti testo, chip sui segmenti citazione.

**Step 3:** Commit: `feat: citation markers rendered as verified chips`

### Task G4: Split-view norma

**Files:**
- Create: `frontend/components/act-panel.tsx`, `frontend/lib/api.ts`

**Step 1:** Click su chip → fetch `GET /acts/{act_ref}` (cache client per act_ref) → pannello destro: titolo, badge vigenza, breadcrumb dell'articolo (da `path`), testo integrale con anchor per articolo; `scrollIntoView` sull'articolo citato; evidenziazione (bg giallo tenue) dei commi citati; link "Vedi su Normattiva".

**Step 2:** Mobile (< lg): il pannello diventa bottom sheet (Sheet di shadcn, `side="bottom"`). Pannello chiudibile, ultima citazione cliccata persiste.

**Step 3:** Verifica manuale desktop + viewport mobile. Commit: `feat: act split-view with scroll and comma highlighting`

### Task G5: Fonti consultate + disclaimer

**Files:**
- Modify: `chat.tsx`; Create: `frontend/components/sources-list.tsx`, `frontend/components/disclaimer.tsx`

**Step 1:** In calce a ogni risposta: collapsible "Fonti consultate (N)" dall'evento `sources` — tutte, anche le non citate; click apre lo split-view. Footer fisso: "Strumento informativo, non costituisce consulenza legale. Fa fede la Gazzetta Ufficiale."

**Step 2:** Commit: `feat: consulted sources list and persistent disclaimer`

---

## Fase H — Deploy su Hetzner

### Task H1: Immagini e compose di produzione

**Files:**
- Create: `backend/Dockerfile` (multi-stage uv), `frontend/Dockerfile` (standalone output), `docker-compose.prod.yml`, `Caddyfile`

**Step 1:** Compose prod: caddy (80/443, reverse proxy → frontend, `/api/*` → fastapi), frontend, fastapi (volume read-only sul clone corpus), qdrant, postgres; volumi `qdrant_data`, `pg_data`, `corpus`; tutte le secret da `.env`. Caddyfile con dominio e TLS automatico.

**Step 2:** Build e smoke test in locale: `docker compose -f docker-compose.prod.yml up` → chat funziona end-to-end.

**Step 3:** Commit: `feat: production docker images and compose`

### Task H2: Runbook deploy + cron

**Files:**
- Create: `docs/deploy.md`

**Step 1:** Documentare passo-passo: provisioning VPS (pattern Wilco: UFW, Docker, backup), clone corpus sul volume, `.env`, primo bootstrap (via `docker compose exec`... o re-uso dell'indice costruito in locale via snapshot Qdrant — documentare entrambe), crontab per `legger ingest delta` notturno, restore da snapshot.

**Step 2:** Eseguire il deploy reale seguendo il runbook (ogni deviazione → correggere il runbook). Verifica end-to-end su dominio.

**Step 3:** Commit: `docs: deployment runbook` — **fine ciclo 1.**

---

## Note per l'esecutore

- **B1 è bloccante:** i pattern di parsing in B3/B4 e le fixture in B2 dipendono dal report. Non implementare il parser su assunzioni non verificate.
- **C6 è un cancello:** nessun task di Fase D+ prima del GO.
- Tutto ciò che chiama Anthropic: id modello e pricing dalla skill `claude-api`, mai a memoria.
- I task G* richiedono il backend su `localhost:8000` per le verifiche manuali.
- Dipendenze tra fasi: D dipende da C (embedder scelto); E dipende da D1 (tabella acts); F dipende da E; G dipende da F; H da tutto.
