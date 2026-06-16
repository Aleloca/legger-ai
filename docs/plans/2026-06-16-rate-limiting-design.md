# Design — Rate limiting su `/chat` (demo pubblica)

- Data: 2026-06-16
- Stato: validato, pronto per la pianificazione dell'implementazione
- Obiettivo: proteggere il budget Anthropic prima di linkare una demo pubblica
  nel README, limitando la concorrenza per-utente e il numero di richieste
  giornaliere per-utente sull'unico endpoint costoso, `POST /chat`.

## Scelte di fondo (decise in brainstorming)

| Tema | Decisione |
| --- | --- |
| Endpoint protetto | Solo `POST /chat` (l'unico che chiama Anthropic) |
| Identità utente | **IP + cookie** combinati ("il più restrittivo vince") |
| Storage contatori | **Redis** (nuovo servizio nel compose) |
| Concorrenza | **Solo per-utente** (max N stream simultanei per IP e per cookie) |
| Limite giornaliero | **Solo per-utente** (no tetto globale — scelta esplicita dell'owner) |
| Comportamento se Redis è giù | **fail-closed** (`429` + log d'allarme) |
| Attivazione | Dietro `RATE_LIMIT_ENABLED`, default `false` in locale |
| Open-source | Inclusa nel repo, configurabile/disattivabile via env |

> Nota budget: è stato proposto un tetto **globale** giornaliero come unico vero
> freno alla spesa massima/giorno (1000 utenti "buoni" sotto-limite svuotano
> comunque il portafoglio). L'owner ha scelto consapevolmente il solo limite
> per-utente. Lasciato qui come possibile estensione futura.

## 1. Architettura & identità

- **Enforcement point**: un *dependency* FastAPI su `POST /chat`, eseguito
  **prima** di aprire lo stream SSE. Un rifiuto è quindi una normale risposta
  HTTP `429` con corpo JSON, non un evento dentro lo stream.
- **IP**: da `X-Forwarded-For` impostato da Caddy. Va preso in modo robusto
  (l'entry inserita da Caddy, non una eventualmente spoofata dal client);
  valutare `trusted_proxies` in Caddy così l'IP non è falsificabile.
- **Cookie**: al primo `/chat` il backend emette un cookie anonimo `lid`
  (UUID random, `HttpOnly`, `SameSite=Lax`, `Secure` in prod).
- **Combinazione**: si valutano i contatori sia per la chiave-IP sia per la
  chiave-cookie; se *uno qualsiasi* dei due supera il limite, la richiesta è
  bloccata. Non basta svuotare i cookie (resta il limite IP) né condividere la
  NAT (resta il limite cookie).
- **Disattivabile**: con `RATE_LIMIT_ENABLED=false` il dependency è un no-op e
  Redis non viene nemmeno contattato.

## 2. Contatori Redis & ciclo di vita

Chiavi (due dimensioni × due identità):

```
daily:ip:<ip>:<YYYY-MM-DD>        contatore giornaliero per IP      TTL: fine giornata
daily:cookie:<lid>:<YYYY-MM-DD>   contatore giornaliero per cookie  TTL: fine giornata
conc:ip:<ip>                      concorrenza in volo per IP        TTL di sicurezza ~5 min
conc:cookie:<lid>                 concorrenza in volo per cookie    TTL di sicurezza ~5 min
```

- **Finestra giornaliera**: giorno di calendario in `Europe/Rome` (coerente col
  messaggio "riprova domani"); la chiave include la data e ha TTL fino a
  mezzanotte → si autoelimina, nessun job di pulizia.
- **Sequenza per richiesta**:
  1. **Check giornaliero** (read-only): se `daily:ip` ≥ limite *o*
     `daily:cookie` ≥ limite → `429 daily_limit` (nessun incremento).
  2. **Acquisizione concorrenza**: `INCR` su `conc:ip` e `conc:cookie`; se uno
     supera il max → rollback (`DECR`) e `429 concurrency_limit`.
  3. **Incremento giornaliero**: `INCR` su `daily:ip` e `daily:cookie`
     (con `EXPIRE` alla prima creazione). Solo qui, all'ammissione effettiva.
  4. **Esecuzione** dello stream.
  5. **Rilascio concorrenza** in un `finally` del generatore SSE: `DECR` su
     `conc:*`. Eseguito sempre, anche su disconnessione del client; il TTL ~5
     min è solo la rete di sicurezza se il processo muore di colpo.
- **Atomicità**: i passi 2–3 come script Lua (o pipeline `MULTI`) →
  check-and-increment atomico, niente race tra richieste simultanee.
- **Cosa conta nella quota**: la richiesta *ammessa* (passo 3), non l'esito.
  Una generazione fallita a metà consuma comunque quota (i token sono già
  spesi nella maggior parte dei casi). Imperfetto ma semplice.

## 3. Configurazione, errori, infrastruttura

Variabili d'ambiente (in `settings.py` + `.env.example`):

```sh
RATE_LIMIT_ENABLED=false          # default off in locale; true in prod
REDIS_URL=redis://localhost:6379  # prod: redis://redis:6379
RATE_LIMIT_PER_USER_CONCURRENT=2  # max stream /chat simultanei per IP e cookie
RATE_LIMIT_PER_USER_DAILY=30      # max richieste/giorno per IP e cookie
RATE_LIMIT_TZ=Europe/Rome         # timezone della finestra giornaliera
```

Risposte `429` (JSON con `code` macchina-leggibile):

| Caso | `code` | Header | Messaggio (IT) |
| --- | --- | --- | --- |
| Limite giornaliero | `daily_limit` | `Retry-After: <sec a mezzanotte>` | "Hai raggiunto il limite di richieste giornaliere per questa demo. Riprova domani." |
| Troppe simultanee | `concurrency_limit` | `Retry-After: 5` | "Hai già una richiesta in corso. Attendi che finisca prima di inviarne un'altra." |

Nessun dettaglio interno nel corpo (in linea con l'`ERROR_MESSAGE` esistente).

**Infrastruttura**: servizio `redis` (`redis:7-alpine`, `--maxmemory` piccolo,
policy `allkeys-lru`, niente persistenza) in `docker-compose.yml` (dev,
opzionale) e `docker-compose.prod.yml`. Caddy invariato salvo garantire un
`X-Forwarded-For` affidabile.

**Fail-closed**: se Redis non è raggiungibile, la richiesta è respinta con
`429` e un log d'allarme.

## 4. Frontend & testing

- **Frontend**: stessa origine (Caddy, backend sotto `/api/backend/*`), quindi
  il cookie `lid` viaggia da solo (`credentials: 'same-origin'` di default).
  Unica modifica in `frontend/lib/sse.ts`: un ramo `response.status === 429`
  che legge il `code` dal JSON e instrada il messaggio giusto sul canale
  `onError` esistente. Nessun nuovo tipo di evento, nessuna modifica CORS.
  ⚠️ `frontend/AGENTS.md`: Next.js con breaking changes — consultare i doc in
  `node_modules/next/dist/docs/` prima di toccare il frontend.
- **Testing backend** (con `fakeredis`/finto): sotto-limite passa; concorrenza
  N+1 → `429 concurrency_limit` + rilascio in `finally`; giornaliero al limite
  → `429 daily_limit`; Redis giù → `429` fail-closed; flag off → no-op; doppia
  chiave (sfora IP ma non cookie e viceversa).
- **Testing frontend**: `streamChat` con una `Response` 429 fittizia → `onError`
  riceve il messaggio corretto per ciascun `code`.

## 5. Documentazione

Sezione README "Demo & rate limiting": variabili d'ambiente, comportamento,
e il link alla demo pubblica.
