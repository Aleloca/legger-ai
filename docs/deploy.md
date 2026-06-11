# Runbook di deploy — legger.ai (Task H2)

Deploy di produzione su VPS Hetzner (Ubuntu 24.04, 8 vCPU, 16 GB RAM, 300 GB
disco), eseguito l'11 giugno 2026. Questo documento registra ogni passo
*esattamente come eseguito* e fa da runbook per i deploy futuri.

Riferimenti: `docker-compose.prod.yml` (header: sequenza di deploy, seeding del
corpus, cron), `Caddyfile`, `backend/Dockerfile`,
`docs/corpus-analysis.md` (appendice collisioni case-insensitive).

**Stato attuale**: `DOMAIN=http://178.105.229.42` (solo HTTP, nessun dominio
ancora puntato). Vedi [§9 Cutover dominio](#9-cutover-dominio--https) quando il
dominio sarà disponibile.

Accesso (chiave SSH, mai password):

```sh
ssh -i ~/.ssh/id_ed25519_legger -o IdentitiesOnly=yes root@178.105.229.42
```

---

## 1. Preparazione del server

Su VPS appena provisionato (come root):

```sh
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" upgrade
apt-get install -y ufw fail2ban git ca-certificates curl

# Docker CE + compose plugin dal repo ufficiale
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
  https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Firewall: solo SSH + HTTP/HTTPS (443/udp = HTTP/3)
ufw allow 22/tcp && ufw allow 80/tcp && ufw allow 443/tcp && ufw allow 443/udp
ufw --force enable
systemctl enable --now fail2ban
```

Versioni installate al deploy: Docker 29.5.3, Compose v5.1.4.

## 2. Trasferimento del repository (git bundle)

Il repo è **solo locale** (nessun remote). Trasferimento via `git bundle`
(porta la storia git completa, niente `.env`/`node_modules`/`.venv`/`.next`):

```sh
# Sul Mac (sorgente)
git -C ~/git/legger-ai bundle create /tmp/legger-ai.bundle cycle-1 main
scp -i ~/.ssh/id_ed25519_legger -o IdentitiesOnly=yes /tmp/legger-ai.bundle root@178.105.229.42:/root/

# Sul VPS
git clone -b cycle-1 /root/legger-ai.bundle /opt/legger-ai
```

Per gli aggiornamenti futuri (finché non esiste un remote): rigenerare il
bundle, ricopiarlo, poi sul VPS `cd /opt/legger-ai && git pull /root/legger-ai.bundle cycle-1`.

## 3. `.env` di produzione

`/opt/legger-ai/.env` (permessi `600`, **mai** committato). Compose lo legge
automaticamente; `DATABASE_URL`/`QDRANT_URL` NON servono qui (gli host interni
`postgres:5432`/`qdrant:6333` sono cablati nel compose):

```sh
DOMAIN=http://178.105.229.42
CORS_ORIGINS=http://178.105.229.42
POSTGRES_PASSWORD=<random forte: openssl rand -hex 24 — esiste SOLO in questo file>
ANTHROPIC_API_KEY=<dal .env locale>
VOYAGE_API_KEY=<dal .env locale>
QDRANT_COLLECTION=norme
EMBEDDER_NAME=voyage-4-large
```

(Più avanti: `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` per gli alert — §11.)

## 4. Seeding del corpus (volume `legger-ai_corpus`)

Il corpus DEVE stare su filesystem case-sensitive (appendice di
`docs/corpus-analysis.md`): il named volume su ext4 del VPS è OK. Il volume va
pre-creato **con le label compose** (altrimenti `up` lo rifiuta come "not
created by Docker Compose"), poi si clona direttamente dentro:

```sh
docker volume create \
  --label com.docker.compose.project=legger-ai \
  --label com.docker.compose.volume=corpus legger-ai_corpus

docker run --rm -v legger-ai_corpus:/corpus alpine sh -c \
  'apk add --no-cache git && git clone https://github.com/ahmeabd/italia-corpus /corpus \
   && chown -R 1000:1000 /corpus'   # uid 1000 = utente "legger" dell'immagine api
```

Eseguito in background (`nohup … &`). Misure reali: **21 min** (pack ~445 MiB
a ~30 MiB/s + checkout di 288.267 file). Sorpresa positiva: i ~73 GB apparenti
del corpus (padding NUL dei Regi decreti, vedi `docs/corpus-analysis.md`)
occupano solo **5,2 GB allocati** sul volume — i blocchi di zeri vengono
scritti sparse, la dimensione apparente dei file resta ~1 MB ciascuno e
`git status` è pulito (contenuto verificato identico a HEAD).

## 5. Build delle immagini sul VPS

Nessun registry: le immagini si buildano sul VPS. Dimensioni misurate:
`legger-api` 9,33 GB (vedi nota SIZE in `backend/Dockerfile`),
`legger-frontend` 306 MB.

```sh
cd /opt/legger-ai
docker compose -f docker-compose.prod.yml build   # ~12 min al primo build
```

## 6. Migrazione dei dati (Mac → VPS)

### 6.1 Postgres (db `legger`: acts, ingestion_runs, ingestion_progress)

Scelta documentata: si ripristina il **dump completo in un db vuoto** e si
SALTA `run --rm migrate`. Il dump `-Fc` contiene schema + dati + la riga di
`alembic_version` (già "stampata" alla head `4ebfcff544f5`), quindi né
`alembic upgrade` né `alembic stamp` sono necessari; i deploy successivi
eseguiranno `migrate` normalmente sulle nuove revisioni. (L'alternativa —
`migrate` poi `pg_restore --data-only --disable-triggers` — è più fragile:
duplica la creazione dello schema e dipende dall'ordine dei vincoli.)

```sh
# Mac: dump dal container dev (il dev stack resta su)
docker exec legger-ai-postgres-1 pg_dump -U legger -Fc legger > /tmp/legger.dump   # 26 MB
scp -i ~/.ssh/id_ed25519_legger -o IdentitiesOnly=yes /tmp/legger.dump root@178.105.229.42:/root/

# VPS: solo postgres+qdrant su, poi restore nel db vuoto
cd /opt/legger-ai
docker compose -f docker-compose.prod.yml up -d postgres qdrant
docker cp /root/legger.dump legger-ai-postgres-1:/tmp/legger.dump
docker exec legger-ai-postgres-1 pg_restore -U legger -d legger --no-owner /tmp/legger.dump
docker exec legger-ai-postgres-1 rm /tmp/legger.dump
```

Verifica eseguita (deve combaciare con la sorgente):

```
acts                = 181870
ingestion_runs      = 4
ingestion_progress  = 287912
alembic_version     = 4ebfcff544f5  (= head di backend/alembic/versions)
```

### 6.2 Qdrant (collezione `norme`, 966.822 punti)

Snapshot API → trasferimento file → recovery API:

```sh
# Mac: snapshot (78 s per 966k punti; file 6.379.766.272 byte ≈ 5,9 GiB)
curl -X POST "http://localhost:6333/collections/norme/snapshots?wait=true"
docker cp legger-ai-qdrant-1:/qdrant/snapshots/norme/<NOME>.snapshot /tmp/norme.snapshot
shasum -a 256 /tmp/norme.snapshot          # annotare l'hash PRIMA del transfer

# Mac → VPS (vedi nota "resume" sotto)
rsync -e "ssh -i ~/.ssh/id_ed25519_legger -o IdentitiesOnly=yes \
          -o ServerAliveInterval=15 -o ServerAliveCountMax=4" \
  --partial --inplace /tmp/norme.snapshot root@178.105.229.42:/root/

# VPS: verificare l'hash, poi recovery dentro il container qdrant.
# NB: 6333 NON è pubblicata sull'host e l'immagine qdrant non ha curl →
# si usa un container curl usa-e-getta sulla rete compose.
sha256sum /root/norme.snapshot             # deve combaciare con l'hash del Mac
docker cp /root/norme.snapshot legger-ai-qdrant-1:/qdrant/snapshots/
docker run --rm --network legger-ai_default curlimages/curl -s --max-time 3000 \
  -X PUT "http://qdrant:6333/collections/norme/snapshots/recover?wait=true" \
  -H "Content-Type: application/json" \
  -d '{"location": "file:///qdrant/snapshots/norme.snapshot"}'
docker exec legger-ai-qdrant-1 rm /qdrant/snapshots/norme.snapshot  # libera 6 GB
# check: GET /collections/norme → points_count == 966822, status green
docker run --rm --network legger-ai_default curlimages/curl -s \
  http://qdrant:6333/collections/norme
```

Eseguito: recovery in **18,7 s** (collezione creata dallo snapshot stesso);
`points_count = 966822`, `status: green`, vettori dense 1024 + sparse bm25.
Spot-check payload via `POST /collections/norme/points/scroll` con filtro
`act_ref=codice-civile, article=2051` → testo corretto dell'art. 2051.

**Lezione del transfer (resume saga)**: il primo tentativo di copia da macOS
si è interrotto più volte (sessione SSH morta a metà file). Cosa funziona:

1. **Keepalive SSH sempre attivi**: `-o ServerAliveInterval=15
   -o ServerAliveCountMax=4` sull'`-e` di rsync (o in `~/.ssh/config`),
   altrimenti i NAT/timeout uccidono la sessione nei transfer lunghi.
2. `rsync --partial --inplace` riprende dal punto morto SOLO se si riusa lo
   stesso file di destinazione (niente suffissi temporanei).
3. **Fallback append su macOS** quando rsync non è praticabile: si guarda la
   dimensione già arrivata sul VPS e si appende il resto con `tail -c`
   (su macOS/BSD `tail -c +N` parte dal byte N, 1-based):

   ```sh
   DONE=$(ssh ... root@178.105.229.42 'stat -c %s /root/norme.snapshot')
   tail -c +$((DONE+1)) /tmp/norme.snapshot | \
     ssh -o ServerAliveInterval=15 ... root@178.105.229.42 \
       'cat >> /root/norme.snapshot'
   ```

   Alla fine **verificare sempre lo sha256** lato VPS prima della recovery
   (un append sbagliato corrompe silenziosamente lo snapshot). Qui:
   `2bf08763…cd7e` verificato identico su entrambi i lati.

### 6.3 Healing delle collisioni case-insensitive

Il clone locale (macOS, APFS case-insensitive) aveva ~353 file collisi
indicizzati con contenuto "shadowed" (appendice `docs/corpus-analysis.md`).
Sul VPS (ext4 case-sensitive) il checkout è corretto, quindi un bootstrap con
resume riprocessa SOLO i file il cui sha non combacia col checkpoint:

```sh
cd /opt/legger-ai
docker compose -f docker-compose.prod.yml run --rm ingest \
  legger ingest bootstrap --embedder voyage-4-large --qdrant-collection norme
```

Atteso: ~181k file saltati dal resume, ~350 riprocessati (costo Voyage:
centesimi). Numeri della run reale in §13.

> **STATO: NON ANCORA ESEGUITO.** Il clone del corpus è completo e verificato
> (288.267 file, `git status` pulito — vedi §4), quindi il comando qui sopra
> è **pronto da lanciare così com'è**. È l'unico passo rimasto per chiudere
> il deploy. Lanciarlo in `nohup`/`tmux` e controllare il log: la quasi
> totalità dei file deve risultare "skipped" dal resume.

## 7. Avvio dello stack e smoke test

```sh
cd /opt/legger-ai
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml ps   # tutti Up/healthy
```

Verifiche attraverso Caddy (dall'esterno, eseguite l'11/06 — tutte OK):

```sh
curl http://178.105.229.42/api/backend/healthz        # 200 {"status":"ok"}
curl -I http://178.105.229.42/                        # 200 text/html (Next)
curl "http://178.105.229.42/api/backend/search?q=art.%202051%20c.c."
# → primo risultato match=exact: codice-civile art. 2051, testo reale
# Smoke SSE (1 chiamata Sonnet): risposta grounded con citazioni verificate
curl -N -X POST http://178.105.229.42/api/backend/chat \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"Cosa prevede l'\''art. 2051 c.c. sul danno cagionato da cose in custodia?"}]}'
# → event: sources (art. 2051/2052/…), 29 event: token,
#   3 event: citation con marker [[codice-civile|art.2051]] verified:true,
#   event: done {"stop_reason":"end_turn"}
```

**Nota se il clone del corpus è ancora in corso** (non era più il caso al
momento dello smoke finale): `GET /api/backend/acts/<ref>` può rispondere
**503** per gli atti il cui file non è ancora stato checked-out — è ATTESO,
non un bug. Sparisce a clone completato (`acts/codice-civile` → 200 verificato).

## 8. Cron (ingestion + backup)

Lo stack non ha scheduler: cron dell'host. Timezone dei job: il server è in
UTC; il delta notturno è voluto alle **06:30 Europe/Rome** → usare la
variabile `CRON_TZ` di cron (supportata da Ubuntu 24.04/vixie-cron).

I job **non sono rientranti** (un delta lento non deve sovrapporsi al
successivo, né a un check-upstream concorrente): ogni riga è wrappata in
`flock -n` su un lockfile condiviso — se il lock è preso, la run viene
saltata (e si vedrà il buco nel log).

Crontab installato (root, `crontab -l` per verificarlo):

```cron
CRON_TZ=Europe/Rome
# Delta ingestion notturna (git pull corpus + reindicizzazione differenziale).
# flock: i job non sono rientranti — se una run precedente è ancora viva, salta.
30 6 * * * /usr/bin/flock -n /var/lock/legger-ingest.lock sh -c "cd /opt/legger-ai && /usr/bin/docker compose -f docker-compose.prod.yml run --rm ingest legger ingest delta" >> /var/log/legger-delta.log 2>&1
# Controllo freshness upstream del corpus (alert Telegram se stantio)
0 8 * * * /usr/bin/flock -n /var/lock/legger-ingest.lock sh -c "cd /opt/legger-ai && /usr/bin/docker compose -f docker-compose.prod.yml run --rm ingest legger ingest check-upstream" >> /var/log/legger-delta.log 2>&1
# Backup settimanale (domenica 04:00): pg_dump + snapshot qdrant, retention 4
0 4 * * 0 /usr/bin/flock -n /var/lock/legger-backup.lock /root/backup-legger.sh >> /var/log/legger-backup.log 2>&1
```

## 9. Cutover dominio + HTTPS

Quando il dominio (es. `legger.ai`) punta a 178.105.229.42 (record A; TTL
basso prima del cutover):

1. In `/opt/legger-ai/.env`: `DOMAIN=https://legger.ai` e
   `CORS_ORIGINS=https://legger.ai`.
2. Ricaricare: Caddy prende DOMAIN dall'ambiente del container, quindi serve
   ricreare i container che leggono `.env`:

   ```sh
   cd /opt/legger-ai
   docker compose -f docker-compose.prod.yml up -d   # ricrea caddy (nuovo DOMAIN) e api (nuovo CORS)
   ```

   Caddy ottiene da solo il certificato Let's Encrypt (porta 80/443 già
   aperte; i certificati persistono nel volume `caddy_data`).
3. Verifica: `curl https://legger.ai/api/backend/healthz` e che il redirect
   HTTP→HTTPS funzioni (automatico in Caddy con site address https).

## 10. Backup

Script `/root/backup-legger.sh` (installato al deploy, cron settimanale §8) —
conserva le ultime 4 settimane in `/root/backups`:

```sh
#!/bin/bash
set -euo pipefail
TS=$(date +%Y%m%d)
mkdir -p /root/backups
# Postgres: dump logico compresso
docker exec legger-ai-postgres-1 pg_dump -U legger -Fc legger > /root/backups/legger-$TS.dump
# Qdrant: snapshot via API. La 6333 NON è pubblicata sull'host e l'immagine
# qdrant non ha curl: si usa un container curl usa-e-getta sulla rete compose.
SNAP=$(docker run --rm --network legger-ai_default curlimages/curl \
  -s -X POST "http://qdrant:6333/collections/norme/snapshots?wait=true" \
  | sed -E "s/.*\"name\":\"([^\"]+)\".*/\1/")
docker cp legger-ai-qdrant-1:/qdrant/snapshots/norme/$SNAP /root/backups/
docker run --rm --network legger-ai_default curlimages/curl \
  -s -X DELETE "http://qdrant:6333/collections/norme/snapshots/$SNAP" >/dev/null
# retention: i 4 più recenti per tipo
ls -t /root/backups/legger-*.dump 2>/dev/null | tail -n +5 | xargs -r rm
ls -t /root/backups/norme-*.snapshot 2>/dev/null | tail -n +5 | xargs -r rm
echo "backup OK $TS"
```

Il corpus non si backuppa (ricostruibile dal clone GitHub).

In aggiunta: **snapshot Hetzner** dell'intero disco prima di ogni deploy
rischioso (Cloud Console → Server → Snapshots; costo ~0,01 €/GB/mese).

## 11. Monitoraggio e alert

- Stato servizi: `docker compose -f docker-compose.prod.yml ps`
  (healthcheck su api/postgres/qdrant; `restart: unless-stopped` ovunque).
- Log: `docker compose -f docker-compose.prod.yml logs -f --tail=100 api`
  (idem `caddy`, `frontend`); ingestion in `/var/log/legger-ingest.log`.
- Alert Telegram (ingestion fallita / errori / upstream stantio — Task D4):
  1. Con BotFather (`@BotFather` su Telegram): `/newbot`, scegliere nome e
     username → ottieni `TELEGRAM_BOT_TOKEN`.
  2. Scrivere un messaggio qualsiasi al bot, poi
     `curl https://api.telegram.org/bot<TOKEN>/getUpdates` →
     `result[0].message.chat.id` è `TELEGRAM_CHAT_ID`.
  3. Aggiungere entrambi a `/opt/legger-ai/.env`. I one-shot `ingest` li
     leggono al prossimo run (nessun riavvio dei servizi necessario).
- Disco: `df -h /` e `docker system df` (l'immagine api è ~6 GB a layer;
  `docker image prune` dopo i rebuild).

## 12. Rollback

Le immagini sono buildate localmente e taggate `legger-api`/`legger-frontend`
(`latest` implicito). Prima di un deploy nuovo, preservare le correnti:

```sh
docker tag legger-api legger-api:prev
docker tag legger-frontend legger-frontend:prev
```

Rollback:

```sh
cd /opt/legger-ai
docker compose -f docker-compose.prod.yml down        # i volumi restano
git checkout <commit-precedente>                       # o ri-taggare :prev → latest
docker tag legger-api:prev legger-api && docker tag legger-frontend:prev legger-frontend
docker compose -f docker-compose.prod.yml up -d --no-build
```

Per i dati: ripristino da `/root/backups` (pg_restore nel db ricreato;
qdrant `snapshots/recover` come in §6.2). Se una migrazione alembic è già
girata, serve il downgrade corrispondente prima del rollback dell'immagine.

## 13. Esito del deploy dell'11 giugno 2026 (misure reali)

| Passo | Esito |
|---|---|
| Prep server (apt upgrade + Docker + UFW + fail2ban) | OK, ~4 min |
| Bundle git (cycle-1+main) | 1,2 MB |
| pg_dump locale | 26 MB, 181.870 acts |
| pg_restore su VPS | 4,1 s; conteggi identici; alembic head verificata |
| Snapshot Qdrant locale | 78 s, 6.379.766.272 byte (966.822 punti) |
| Trasferimento snapshot Mac→VPS | OK con riprese multiple (vedi §6.2, lezione keepalive + tail -c); sha256 `2bf08763…cd7e` verificato identico |
| Clone corpus nel volume | OK, 21 min: 288.267 file, `git status` pulito; 5,2 GB allocati (zeri sparse, ~73 GB apparenti) |
| Build immagini | OK: legger-api 9,33 GB, legger-frontend 306 MB |
| Recovery snapshot su VPS | 18,7 s; points_count 966822, status green; spot-check art. 2051 c.c. OK |
| Healing bootstrap (collisioni) | **NON ESEGUITO** — clone completo, comando pronto (§6.3/§14) |
| `up -d` stack completo | OK: caddy/frontend/api/postgres/qdrant tutti Up, healthcheck verdi |
| Crontab + backup script | Installati (flock, §8/§10) |
| Smoke /healthz, /, /search, /chat SSE | Tutti OK dall'esterno via Caddy (§7); /acts 503 atteso per file non ancora clonati |

## 14. Passi manuali rimasti (in ordine di urgenza)

1. **Ruotare SUBITO la password root** del VPS: è stata condivisa in chat
   durante il provisioning, quindi va considerata compromessa. Da sessione
   SSH con chiave: `passwd`.
2. **Dopo la rotazione**, disabilitare l'auth a password in
   `/etc/ssh/sshd_config` (`PasswordAuthentication no`, poi
   `systemctl reload ssh`) — la chiave `id_ed25519_legger` è già configurata
   e va verificato il login con chiave PRIMA di chiudere la sessione.
   fail2ban resta comunque attivo su sshd.
3. **Healing bootstrap** (§6.3): il clone del corpus è completo e verificato,
   il comando è pronto da lanciare:

   ```sh
   cd /opt/legger-ai
   docker compose -f docker-compose.prod.yml run --rm ingest \
     legger ingest bootstrap --embedder voyage-4-large --qdrant-collection norme
   ```

4. Puntare il dominio ed eseguire il cutover HTTPS (§9).
5. Configurare gli alert Telegram (§11).
