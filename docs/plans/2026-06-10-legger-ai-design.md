# Legger.ai — Design Document

**Versione:** 0.2
**Data:** 10 giugno 2026
**Autore:** Alessandro
**Stato:** Validato per l'implementazione del primo ciclo (Fase 0 + Fase 1 + UI chat). Nome provvisorio `legger.ai` — verificare disponibilità dominio e marchio prima del lancio.

> Changelog v0.1 → v0.2: risolta l'architettura runtime (servizio FastAPI), definita la struttura monorepo, ristretto lo scope del primo ciclo di build, dettagliati spike e criteri di go/no-go, definito il protocollo citazioni end-to-end.

---

## 1. Visione

Un assistente conversazionale iper-verticale sulla legislazione italiana, costruito sopra il corpus pubblico [italia-corpus](https://github.com/ahmeabd/italia-corpus) (>250.000 atti da Normattiva, aggiornato quotidianamente, un file Markdown per norma, un commit git per ogni modifica normativa).

La promessa del prodotto: **risposte in linguaggio naturale, sempre ancorate al testo esatto della norma vigente, con citazione puntuale (atto, articolo, comma) e possibilità di leggere la fonte a fianco della risposta.**

Il differenziante rispetto a un LLM generico non è "sapere la legge" ma tre cose che un LLM generico non può garantire:

1. **Grounding verificabile** — ogni affermazione è collegata al testo normativo recuperato, non alla memoria del modello.
2. **Vigenza** — distinzione esplicita tra norme vigenti, abrogate e DL decaduti.
3. **Dimensione temporale** — grazie alla storia git del corpus, è possibile rispondere a "com'era l'art. X al 15 marzo 2024?", una capacità che quasi nessun competitor offre.

### 1.1 Non-goals (MVP)

- Non è consulenza legale: il prodotto informa, non consiglia. Disclaimer esplicito e persistente.
- Niente giurisprudenza (sentenze) nell'MVP: solo testi normativi. La giurisprudenza è in roadmap come fase successiva.
- Niente normativa regionale, circolari, prassi (AdE, INPS, INAIL): fuori scope finché il core non è solido.
- Niente redazione di atti/documenti: solo consultazione e spiegazione.
- Niente app mobile nativa: web responsive.

---

## 2. Target e casi d'uso

Priorità dichiarata: **1) avvocati e professionisti legali, 2) PMI/compliance, 3) sviluppatori (API), 4) cittadini.**

L'MVP si progetta per il target 1, che è anche il più esigente: un professionista legale non tollera citazioni inventate e abbandona il prodotto al primo errore grave. Questo vincolo guida tutte le scelte di retrieval e generation (precisione > creatività).

| Persona | Caso d'uso tipico | Feature critica |
|---|---|---|
| Avvocato | "Cosa prevede l'art. 2051 c.c. e quali norme lo richiamano?" | Citazioni esatte, testo a fianco, rinvii |
| Praticante/studente | "Spiegami la differenza tra dolo eventuale e colpa cosciente nel c.p." | Spiegazione + fonti |
| Consulente PMI | "Quali obblighi introduce il D.Lgs. X per le imprese sotto i 50 dipendenti?" | Vigenza, decorrenze |
| Fiscalista | "Com'era questa norma prima della legge di bilancio 2026?" | Versioning temporale |

### 2.1 Modello utenti

Account singoli nell'MVP (email + password o magic link; OAuth Google opzionale). Team/organizzazioni in roadmap: lo schema dati nasce già con `organization_id` nullable sulle entità principali per non dover migrare dopo.

---

## 3. Scope

### 3.1 Scope del primo ciclo di build (questo plan)

Deciso in fase di validazione del design: **Fase 0 (spike) + Fase 1 (core pipeline) + UI chat con split-view**, ovvero una fetta anticipata della Fase 2 ridotta all'osso:

- Chat multi-turno in streaming con citazioni cliccabili.
- Split-view norma (pannello laterale con testo integrale, scroll all'articolo, evidenziazione commi; bottom sheet su mobile).
- **Senza auth, senza storico persistente, senza retention settings**: interfaccia anonima, conversazione mantenuta lato client. Il minimo per validare l'esperienza chiave e usarla in prima persona.
- Deploy funzionante sul VPS Hetzner.

### 3.2 Scope MVP completo (cicli successivi)

3. **Ricerca/navigazione norme** — ricerca per estremi e full-text, pagine norma pubbliche indicizzabili (entry point SEO). L'endpoint `GET /search` nasce già nel primo ciclo.
4. **Versioning temporale** — selettore di data sulla pagina norma e supporto in chat a domande con riferimento temporale.
5. **Auth, storico conversazioni, retention settings** (la vera Fase 2).

---

## 4. Architettura

```
Browser ── Next.js (UI, proxy) ── FastAPI ──┬── Qdrant   (vettori dense + sparse)
                                            ├── Postgres 17 (metadata atti)
                                            ├── Anthropic API (Haiku + Sonnet)
                                            └── clone italia-corpus (git, per versioning)

Ingestion worker (stesso package Python, entrypoint CLI separato, cron)
```

Decisione v0.2: la pipeline di retrieval + generation vive in un **servizio Python FastAPI**, non nelle API routes di Next.js. Motivi: il reranker self-hosted (`bge-reranker-v2-m3`) e l'eventuale embedding self-hosted (`bge-m3`) girano in Python; tutta la pipeline ML resta in un linguaggio solo; il codice dello spike CLI evolve direttamente nel servizio senza riscritture; il modulo di parsing è condiviso tra ingestion e API. Next.js è frontend puro che proxy-a verso FastAPI.

**Endpoint FastAPI:**

- `POST /chat` → SSE con eventi tipizzati (`status`, `token`, `citation`, `citation_unverified`, `sources`, `done`). La conversazione è passata dal client a ogni richiesta (stateless, coerente con l'assenza di auth).
- `GET /acts/{act_ref}` → testo integrale dell'atto, parsato nella stessa struttura usata dall'ingestion (per lo split-view).
- `GET /search` → ricerca diretta per estremi e full-text.

**Struttura monorepo:**

```
legger-ai/
├── backend/              # progetto Python unico (uv)
│   ├── legger/
│   │   ├── corpus/       # parsing MD, chunking — condiviso da ingestion e API
│   │   ├── ingestion/    # bootstrap + delta git-driven
│   │   ├── retrieval/    # hybrid search, rerank, citation-following
│   │   ├── chat/         # query understanding, prompt, streaming
│   │   └── api/          # FastAPI app
│   └── tests/
├── frontend/             # Next.js
├── docs/plans/
└── docker-compose.yml    # qdrant + postgres (dev locale)
```

Principio: `corpus/` è l'unica implementazione della verità sulla struttura degli atti — la usano sia l'ingestion sia il rendering dello split-view.

**Hosting:** tutto su un singolo VPS Hetzner (Docker Compose, Caddy reverse proxy, UFW, backup giornalieri — stack già rodato). In produzione: 5 container (Caddy, Next.js, FastAPI, Qdrant, Postgres), volumi dedicati, clone del corpus su volume persistente, cron per il delta. Scaling successivo: separare Qdrant su un secondo VPS quando l'indice cresce. Sviluppo in locale (Mac) con Docker Compose per Qdrant/Postgres.

### 4.1 Pipeline di ingestion

Due entrypoint sullo stesso codice:

- **`legger ingest bootstrap`** — intero corpus, con checkpoint/resume su Postgres (tabella `ingestion_progress`: file processato → sha). Interrompibile e ripartibile. >250.000 atti è un volume serio: l'ordine di grandezza atteso è 1–3M chunk, da verificare nello spike.
- **`legger ingest delta`** — schedulato (cron giornaliero, dopo l'orario di aggiornamento tipico del repo upstream):
  1. `git pull` sul clone locale del corpus.
  2. `git diff --name-status HEAD@{1} HEAD` → lista dei soli file aggiunti/modificati/eliminati. **Si re-indicizza solo il delta.**
  3. Per ogni file toccato: parsing → chunking → embedding → upsert su Qdrant + aggiornamento metadata su Postgres.
  4. File eliminati o spostati in collezioni "abrogati/decaduti" → aggiornamento del flag di vigenza, **senza cancellare i punti** (servono per il versioning temporale).
  5. Log strutturato su `ingestion_runs` + alert (email/Telegram) se il pull fallisce o se il repo upstream non riceve commit da >7 giorni (early warning di abbandono upstream).

### 4.2 Parsing e chunking

Il chunking è la decisione più importante dell'intero sistema. Regole:

- **Unità base: l'articolo.** Mai chunking a caratteri fissi che taglia un comma a metà. Il Markdown del corpus ha struttura per intestazioni: si parsa la gerarchia atto → (libro/titolo/capo) → articolo → commi.
- Articoli molto lunghi (es. articoli unici delle leggi di bilancio con centinaia di commi) → split per gruppi di commi, con overlap di 1 comma e header ripetuto in ogni chunk ("L. 197/2022, art. 1, commi 100–120").
- **Ogni chunk porta un'intestazione contestuale** prefissata al testo prima dell'embedding: tipo atto, estremi, titolo dell'atto, rubrica dell'articolo. Questo migliora drasticamente il retrieval semantico.
- Atti brevissimi (DM di una pagina) → un chunk unico.

**Metadata per chunk (payload Qdrant + tabella Postgres):**

| Campo | Esempio | Uso |
|---|---|---|
| `act_type` | `decreto_legislativo` | filtri, badge UI |
| `act_ref` | `dlgs-81-2008` | chiave canonica |
| `act_title` | "Testo unico sicurezza lavoro" | display, embedding header |
| `article` | `art. 18` | citazioni, deep-link |
| `commi` | `[1, 2, 3]` | citazioni puntuali |
| `collection` | `Testi Unici` | mapping cartella repo |
| `vigenza` | `vigente \| abrogato \| decaduto` | filtro di default |
| `file_path` | path nel repo | versioning via git |
| `last_commit` | sha + data | freshness, audit |

La **vigenza si deriva dalla struttura a cartelle del repo** (es. "Atti normativi abrogati (in originale)", "DL decaduti") — è già curata upstream, non va reinventata.

### 4.3 Retrieval

**Hybrid search obbligatorio** — il dominio legale ha due tipi di query radicalmente diversi:

- Riferimenti esatti ("art. 613-bis c.p.", "D.L. 77/2021"): vince il match lessicale.
- Domande in linguaggio naturale ("posso licenziare un dipendente in malattia?"): vince la semantica.

Pipeline per ogni messaggio utente:

1. **Query understanding** (Claude Haiku, output JSON tipizzato via tool use, non parsing di testo libero): estrae estremi normativi espliciti, riferimenti temporali, e riscrive la query per il retrieval tenendo conto del contesto conversazionale (risoluzione di "e il comma successivo?").
2. **Fast path:** se la query contiene estremi espliciti e risolvibili (regex su pattern noti) → lookup diretto su Postgres per `act_ref` + `article`, bypass del vettoriale.
3. **Hybrid retrieval su Qdrant:** dense (modello scelto nel benchmark dello spike — candidati: `bge-m3` self-hosted vs API commerciale tipo Voyage) + sparse (BM25 nativo Qdrant), fusione RRF. Filtro di default `vigenza = vigente`, rimosso solo se la query chiede esplicitamente norme storiche.
4. **Reranking** dei top ~50 → top 8–12 (`bge-reranker-v2-m3` su CPU). Il delta del rerank si misura sul set di query di valutazione: se è marginale, si tiene la pipeline senza rerank (latenza in meno).
5. **Citation-following (1 hop):** se i chunk recuperati contengono rinvii espliciti ad altre norme ("di cui all'articolo...", "ai sensi del..."), fetch diretto degli articoli richiamati e aggiunta al contesto, con budget massimo di token.

### 4.4 Generation

- **Modello principale: Claude Sonnet (attuale `claude-sonnet-4-6`)**; Haiku (`claude-haiku-4-5`) per query understanding e task ausiliari. Opus/Fable riservato a un eventuale tier premium futuro. Verificare modelli e prezzi correnti su https://docs.claude.com prima dell'implementazione.
- **System prompt con regole ferree:** rispondere SOLO sulla base dei passaggi forniti; ogni affermazione normativa deve citare atto+articolo+comma; se il contesto non basta, dichiararlo e suggerire come riformulare; mai inventare estremi; tono professionale, italiano giuridico ma leggibile.
- **Formato citazioni strutturato** nell'output: marker `[[dlgs-81-2008|art.18|c.1]]` che il frontend trasforma in chip cliccabili collegati allo split-view.
- **Streaming** via SSE.
- **Prompt caching** sul system prompt e sul contesto normativo nei turni successivi della stessa conversazione.
- **Guardrail post-generazione (cheap):** ogni marker emesso viene validato contro l'insieme dei chunk forniti al modello; marker orfano → evento SSE `citation_unverified`, renderizzato come chip ambra "citazione non verificata".

### 4.5 Versioning temporale (ciclo successivo)

La feature distintiva. Implementazione a due livelli:

- **Pagina norma:** selettore data → `git log --follow -- <file>` per trovare il commit vigente a quella data → `git show <sha>:<file>` per il testo. Nessun database storico da costruire: git È il database storico. Cache dei risultati (le versioni passate sono immutabili).
- **In chat:** se la query understanding rileva un riferimento temporale, il sistema recupera la versione storica del file via git e la inserisce nel contesto al posto (o a fianco) della vigente, segnalando esplicitamente che si sta citando un testo storico.
- **Diff view:** confronto tra due versioni di una norma (`git diff` renderizzato) — basso costo, alto valore percepito.

Limite da dichiarare in UI: la storia parte dal primo commit del repo (≈2025). Per il pregresso, Normattiva resta la fonte.

---

## 5. Data model

**PostgreSQL (fonte di verità per metadata e dati utente):**

- `acts` — un record per atto: `act_ref` (PK logica), tipo, estremi, titolo, collezione, vigenza, file_path, last_commit_sha, last_updated.
- `ingestion_runs` — log delle run (commit range, file processati, errori).
- `ingestion_progress` — checkpoint del bootstrap (file → sha) per resume.
- Cicli successivi: `users` (con `organization_id` nullable), `conversations` / `messages` (con `retention_mode`), `message_citations`, `message_feedback`.

**Qdrant:**

- Collezione unica `norme` con named vectors (dense + sparse) e payload come da §4.2. Filtri indicizzati su `vigenza`, `act_type`, `act_ref`.
- Snapshot periodici su storage Hetzner — ma l'indice è sempre ricostruibile dal corpus, quindi il backup critico resta Postgres.

---

## 6. UX

**Layout primo ciclo (desktop):** due colonne — chat · pannello norma (split-view, apribile/chiudibile). Niente sidebar conversazioni finché non c'è storico. Su mobile il pannello diventa un bottom sheet.

**Flusso citazioni end-to-end:**

1. Il backend emette i marker `[[act_ref|art|comma]]` nel testo e, via SSE, eventi `citation` con metadati risolti (titolo atto, rubrica articolo, stato vigenza).
2. Il frontend trasforma i marker in chip: `D.Lgs. 81/2008, art. 18, c. 1` con badge vigenza (verde vigente / grigio abrogato / ambra non verificata).
3. Click sul chip → `GET /acts/{act_ref}` → il pannello carica il testo integrale, scrolla all'articolo citato, evidenzia i commi rilevanti. Breadcrumb (atto → titolo → capo → articolo) e link alla fonte su Normattiva.

**Stati di trasparenza:** indicatore "sto cercando nel corpus…" (evento SSE `status`); in calce a ogni risposta l'elenco espandibile delle **fonti consultate** (tutti i chunk passati al modello, anche i non citati); badge "testo storico" quando si cita una versione non vigente (ciclo versioning).

**Stack frontend:** Next.js App Router, SSE consumato client-side, Tailwind + shadcn/ui, rendering Markdown con plugin custom per i marker citazione.

**Disclaimer:** footer persistente fin dal primo ciclo: "Strumento informativo, non costituisce consulenza legale. Fa fede la Gazzetta Ufficiale." Banner alla prima sessione quando ci saranno gli account.

**Pagine norma pubbliche** (`/norma/dlgs-81-2008`) indicizzabili: ciclo successivo (utilità + canale SEO).

---

## 7. Privacy, GDPR e segreto professionale

Il target avvocati rende la riservatezza un argomento di vendita, non solo un obbligo. (Rilevante dai cicli con auth/persistenza; il primo ciclo non persiste conversazioni per costruzione.)

- **Retention a scelta dell'utente** (per-account, override per-conversazione): storico completo · 30 giorni · conversazioni effimere.
- Hosting interamente UE (Hetzner, Germania/Finlandia) — punto di forza da comunicare.
- Dati verso Anthropic API: verificare e documentare le policy correnti di data retention/training (https://docs.claude.com) e riportarle nella privacy policy. Valutare le opzioni contrattuali per il commercial use.
- Niente training su dati utente da parte nostra. Logging applicativo senza contenuto delle conversazioni (solo metadata) in modalità effimera.
- Registro trattamenti, DPA con i sub-processor (Hetzner, Anthropic), cookie banner minimale.

---

## 8. Rischi e mitigazioni

| Rischio | Impatto | Mitigazione |
|---|---|---|
| **Abbandono del repo upstream** (singolo maintainer) | Fatale: il prodotto perde la fonte dati | Mirror completo locale (il clone git È il mirror, con tutta la storia); alert se nessun commit upstream per >7 giorni; contatto con l'autore (valutare contributi o sponsorship); exit strategy: ricostruire la pipeline dalle API pubbliche Normattiva (il README upstream ne descrive l'architettura) |
| **Errori di parsing upstream** | Risposte basate su testo corrotto | Validazioni in ingestion (struttura attesa, encoding, lunghezze anomale) + link sempre presente a Normattiva; segnalare i bug upstream |
| **Allucinazioni / citazioni errate** | Perdita di fiducia del target professionale | Grounding rigido, guardrail marker post-generazione, fonti consultate sempre visibili, UI che incoraggia la verifica sul testo a fianco |
| **Vigenza errata** | Grave per uso professionale | Vigenza derivata dalle collezioni upstream + badge esplicito su ogni citazione + filtro default `vigente` |
| **Qualità retrieval insufficiente** | Il prodotto non mantiene la promessa | Lo spike (Fase 0) esiste per questo: go/no-go su recall misurato prima di costruire il resto |
| **Costi API fuori controllo** | Insostenibile come side project | Prompt caching, Haiku per i task ausiliari, rate limit per utente free, budget alert |
| **Responsabilità legale** | Contestazioni per risposte errate | Disclaimer persistente, ToS con limitazione di responsabilità (review legale prima del lancio), posizionamento come strumento di ricerca documentale |
| **Concorrenza** | Commoditizzazione | Differenzianti difendibili: versioning temporale, trasparenza delle fonti, prezzo aggressivo da costi infra ridotti |

---

## 9. Qualità e test

**Codice deterministico → TDD:** parser, chunker, estrazione estremi (regex fast path), guardrail citazioni, logica di vigenza — con fixture prese dai file reali del corpus (gli edge case trovati nell'analisi diventano test).

**Qualità retrieval → misurazione:** set di ~30 query legali reali con chunk atteso noto, costruito nello spike (serve anche al benchmark embedding: recall@10, MRR). Cresce a ~50 query (mix: lookup diretti, domande interpretative, trabocchetti su norme abrogate, domande temporali) ed è eseguibile come `legger eval` a ogni modifica di retrieval o prompt. È l'embrione del golden dataset: quando il prodotto valida, si promuove a eval automatica (LLM-as-judge) senza ripartire da zero.

**Frontend:** test leggeri sui componenti critici (parsing marker → chip). Niente E2E nel primo ciclo.

**Feedback in-app** (cicli successivi): 👍/👎 su ogni risposta con motivo opzionale → `message_feedback`.

---

## 10. Roadmap

**Fase 0 — Spike (validazione tecnica).** Campione: collezione `Codici` (la più interrogata e la più ostica strutturalmente — se il chunking regge lì, regge ovunque). Il codice dello spike sopravvive in `legger/corpus/` e `legger/retrieval/`.

1. Clone e analisi del corpus: distribuzione collezioni, consistenza delle intestazioni Markdown, casi patologici. Output: report in `docs/` che fissa le assunzioni del parser.
2. Parser + chunker su `Codici`, con test.
3. Benchmark embedding: `bge-m3` self-hosted (Mac, MPS) vs API commerciale, su ~30 query → recall@10, MRR. Risolve la questione aperta #3.
4. Indice Qdrant sul campione (dense + sparse, RRF) + chat CLI minimale (senza rerank).

**Criteri di go/no-go:** recall@10 ≥ ~85%; fast path al 100% sui pattern noti; stima fondata di chunk totali e costo bootstrap embedding. Se il recall delude → iterare su chunking/header prima di procedere.

**Fase 1 — Core pipeline.** Ingestion bootstrap + delta diff-driven, hybrid retrieval completo (query understanding, rerank, fast path, citation-following), API `/chat` SSE con citazioni strutturate e guardrail, `/acts`, `/search`.

**Fase 1.5 — UI chat (questo ciclo).** Next.js: chat streaming, chip citazioni, split-view, fonti consultate, disclaimer. Senza auth né storico. Deploy su Hetzner (manuale documentato; CI/CD rimandata).

**Fase 2 — Prodotto.** Auth, storico conversazioni, retention settings, pagine norma pubbliche, ricerca, CI/CD Bitbucket Pipelines.

**Fase 3 — Versioning temporale.** Selettore data, diff view, supporto temporale in chat.

**Fase 4 — Beta privata.** 10–20 utenti del target, foglio qualità, iterazione su retrieval e UX. Qui si decide il modello di business con dati reali.

**Post-MVP (backlog):** API pubblica · team/organizzazioni · alert su modifiche normative (watch di una norma → email al commit che la tocca) · giurisprudenza · normativa regionale · export/condivisione risposte.

---

## 11. Stima costi di esercizio (ordine di grandezza)

| Voce | Stima mensile |
|---|---|
| VPS Hetzner (CX42/CPX41-class, 16GB RAM) | ~30–50 € |
| Storage backup + snapshot | ~5 € |
| Dominio .ai | ~6–8 € (annuale ammortizzato) |
| Anthropic API (beta, basso traffico, con caching) | variabile: ~20–100 € |
| Embedding (bootstrap una tantum se via API) | da stimare nello spike; self-hosted = solo tempo macchina |
| **Totale fase beta** | **< 150 €/mese** |

---

## 12. Questioni aperte

1. **Nome e dominio** — verificare disponibilità `legger.ai` (fallback `sapr.ai`, `dir.ai`, `normia.ai`); ricerca marchio su EUIPO/TMview.
2. **Modello di business** — da definire dopo la beta. Ipotesi: free con limiti + Pro individuale (~15–25 €/mese); API a consumo dopo.
3. ~~Modello di embedding~~ → si decide nello spike, con benchmark (§10 Fase 0.3).
4. **Rapporto con l'autore del corpus** — aprire il contatto presto: sponsorship, contributi o accordo informale riducono il rischio #1.
5. **Review legale** di ToS, privacy policy e disclaimer prima del lancio pubblico.
