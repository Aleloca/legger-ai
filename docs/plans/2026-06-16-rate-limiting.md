# Rate Limiting (`/chat`) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add per-user (IP + cookie) concurrency and daily request limits to `POST /chat` so a public demo cannot run up the Anthropic bill, configurable and disable-able via env.

**Architecture:** A `RateLimiter` (Redis-backed) is built in the app lifespan and stored on `app.state.rate_limiter` (or `None` when disabled). The `POST /chat` handler identifies the caller by IP (from `X-Forwarded-For`) and an anonymous `lid` cookie, runs a check-and-acquire against four Redis keys (daily + concurrency, each per IP and per cookie), returns HTTP `429` with a machine-readable `code` when a limit is hit, and releases the concurrency slot in the SSE generator's `finally`. Fail-closed if Redis is unreachable.

**Tech Stack:** FastAPI, redis-py (sync), fakeredis (tests), pydantic-settings; frontend `fetch`/TS (`lib/sse.ts`) with vitest.

**Reference:** design doc `docs/plans/2026-06-16-rate-limiting-design.md`.

**Conventions:** run all backend commands from `backend/` with `uv run`. The full suite is slow (~3 min); during TDD run only the targeted test file/node. Keep messages in Italian, no internals leaked (mirror existing `ERROR_MESSAGE`).

---

### Task 1: Add dependencies (redis + fakeredis)

**Files:**
- Modify: `backend/pyproject.toml` (deps + dev group)

**Step 1: Add runtime + dev deps**

In `[project].dependencies` add: `"redis>=5.0"`.
In `[dependency-groups].dev` add: `"fakeredis>=2.21"`.

**Step 2: Sync**

Run: `uv sync`
Expected: resolves and installs `redis` and `fakeredis`.

**Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build: add redis + fakeredis for rate limiting"
```

---

### Task 2: Settings fields

**Files:**
- Modify: `backend/legger/settings.py`
- Test: `backend/tests/test_settings.py`

**Step 1: Write the failing test**

```python
def test_rate_limit_defaults():
    s = Settings(_env_file=None)
    assert s.rate_limit_enabled is False
    assert s.redis_url == "redis://localhost:6379"
    assert s.rate_limit_per_user_concurrent == 2
    assert s.rate_limit_per_user_daily == 30
    assert s.rate_limit_tz == "Europe/Rome"
```

**Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_settings.py::test_rate_limit_defaults -v`
Expected: FAIL (AttributeError / unknown field).

**Step 3: Add the fields** to `Settings` (after `cors_origins`):

```python
    #: Rate limiting on POST /chat (per-user, IP + cookie). Off by default so
    #: local dev is never throttled; prod sets RATE_LIMIT_ENABLED=true. See
    #: docs/plans/2026-06-16-rate-limiting-design.md and legger/api/ratelimit.py.
    rate_limit_enabled: bool = False
    redis_url: str = "redis://localhost:6379"
    #: Max simultaneous /chat streams per IP and per cookie.
    rate_limit_per_user_concurrent: int = 2
    #: Max /chat requests per calendar day, per IP and per cookie.
    rate_limit_per_user_daily: int = 30
    #: Timezone of the daily window (IANA name); the counter resets at its midnight.
    rate_limit_tz: str = "Europe/Rome"
```

**Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_settings.py::test_rate_limit_defaults -v`
Expected: PASS

**Step 5: Commit**

```bash
git add legger/settings.py tests/test_settings.py
git commit -m "feat: add rate-limit settings fields"
```

---

### Task 3: Caller identity (IP from XFF + lid cookie)

**Files:**
- Create: `backend/legger/api/ratelimit.py`
- Test: `backend/tests/test_ratelimit.py`

Identity is pure (no Redis), so test it in isolation first.

**Step 1: Write the failing test**

```python
import uuid
from types import SimpleNamespace

from legger.api.ratelimit import client_ip, ensure_lid, COOKIE_NAME


def _req(headers=None, cookies=None, client_host="10.0.0.1"):
    return SimpleNamespace(
        headers=headers or {},
        cookies=cookies or {},
        client=SimpleNamespace(host=client_host),
    )


def test_client_ip_prefers_rightmost_forwarded_for():
    # Caddy appends the real client last; a spoofed left entry must not win.
    req = _req(headers={"x-forwarded-for": "1.2.3.4, 203.0.113.9"})
    assert client_ip(req) == "203.0.113.9"


def test_client_ip_falls_back_to_peer():
    assert client_ip(_req()) == "10.0.0.1"


def test_ensure_lid_reuses_valid_cookie():
    existing = str(uuid.uuid4())
    lid, is_new = ensure_lid(_req(cookies={COOKIE_NAME: existing}))
    assert lid == existing and is_new is False


def test_ensure_lid_mints_uuid_when_missing_or_invalid():
    lid, is_new = ensure_lid(_req(cookies={COOKIE_NAME: "not-a-uuid"}))
    assert is_new is True
    uuid.UUID(lid)  # parses => valid
```

**Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_ratelimit.py -v`
Expected: FAIL (module/functions missing).

**Step 3: Implement identity helpers** in `backend/legger/api/ratelimit.py`:

```python
"""Per-user rate limiting for POST /chat (IP + anonymous cookie, Redis-backed).

Design: docs/plans/2026-06-16-rate-limiting-design.md. Disabled unless
Settings.rate_limit_enabled; when disabled the app never builds a RateLimiter
and the /chat handler skips all of this.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

logger = logging.getLogger(__name__)

COOKIE_NAME = "lid"
#: Safety TTL on concurrency keys: reclaim a slot if a worker dies before its
#: finally runs. Generous vs the longest plausible /chat stream.
CONC_TTL_S = 300


def client_ip(request) -> str:
    """The caller IP, resistant to a single trusted proxy (Caddy).

    Caddy appends the real peer as the LAST X-Forwarded-For entry, so the
    rightmost value is authoritative even if the client pre-seeded the header.
    Falls back to the socket peer when the header is absent (no proxy / tests).
    """
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[-1].strip()
    return request.client.host


def ensure_lid(request) -> tuple[str, bool]:
    """Return ``(lid, is_new)`` for the anonymous cookie, minting a UUID when
    the cookie is missing or not a valid UUID."""
    raw = request.cookies.get(COOKIE_NAME)
    if raw:
        try:
            uuid.UUID(raw)
            return raw, False
        except ValueError:
            pass
    return str(uuid.uuid4()), True
```

**Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_ratelimit.py -v`
Expected: PASS (4 passed)

**Step 5: Commit**

```bash
git add legger/api/ratelimit.py tests/test_ratelimit.py
git commit -m "feat: rate-limit caller identity (ip + lid cookie)"
```

---

### Task 4: RateLimiter check-and-acquire / release (Redis)

**Files:**
- Modify: `backend/legger/api/ratelimit.py`
- Test: `backend/tests/test_ratelimit.py`

Counters use plain `INCR`/`GET`/`DECR`/`EXPIRE` (no Lua) so fakeredis exercises the real path. Daily is checked read-only first, incremented only on admission. Concurrency is INCR-then-rollback. Redis errors raise `RateLimitError("unavailable")` → fail-closed `429`.

**Step 1: Write the failing tests**

```python
import fakeredis
import pytest

from legger.api.ratelimit import RateLimiter, RateLimitError


def _limiter(concurrent=2, daily=3):
    return RateLimiter(
        fakeredis.FakeStrictRedis(decode_responses=True),
        concurrent=concurrent,
        daily=daily,
        tz="Europe/Rome",
    )


def test_acquire_release_allows_repeat_within_limits():
    rl = _limiter()
    lease = rl.acquire("1.1.1.1", "lidA")   # daily 1/3, conc 1/2
    rl.release(lease)
    lease = rl.acquire("1.1.1.1", "lidA")   # daily 2/3, conc 1/2 again
    rl.release(lease)


def test_concurrency_limit_blocks_then_recovers():
    rl = _limiter(concurrent=1)
    lease = rl.acquire("1.1.1.1", "lidA")
    with pytest.raises(RateLimitError) as ei:
        rl.acquire("1.1.1.1", "lidA")
    assert ei.value.code == "concurrency_limit"
    rl.release(lease)
    rl.acquire("1.1.1.1", "lidA")  # slot freed -> ok


def test_blocked_concurrency_does_not_consume_daily():
    rl = _limiter(concurrent=1, daily=2)
    rl.acquire("1.1.1.1", "lidA")                 # daily 1/2
    with pytest.raises(RateLimitError):
        rl.acquire("1.1.1.1", "lidA")             # blocked on conc, daily untouched
    # second identity proves daily for lidA is still at 1: reuse after release
    # (can't release here; assert via redis directly)
    assert rl.redis.get("daily:cookie:lidA:" + rl._today()) == "1"


def test_daily_limit_blocks_with_retry_after():
    rl = _limiter(concurrent=5, daily=2)
    rl.release(rl.acquire("1.1.1.1", "lidA"))
    rl.release(rl.acquire("1.1.1.1", "lidA"))
    with pytest.raises(RateLimitError) as ei:
        rl.acquire("1.1.1.1", "lidA")
    assert ei.value.code == "daily_limit"
    assert ei.value.retry_after > 0


def test_daily_limit_triggers_on_either_identity():
    rl = _limiter(concurrent=5, daily=1)
    rl.release(rl.acquire("1.1.1.1", "lidA"))     # ip and lidA both at 1/1
    with pytest.raises(RateLimitError) as ei:     # same ip, fresh cookie
        rl.acquire("1.1.1.1", "lidB")
    assert ei.value.code == "daily_limit"


def test_redis_down_fails_closed():
    class Boom:
        def __getattr__(self, _):
            raise ConnectionError("redis down")
    rl = RateLimiter(Boom(), concurrent=2, daily=3, tz="Europe/Rome")
    with pytest.raises(RateLimitError) as ei:
        rl.acquire("1.1.1.1", "lidA")
    assert ei.value.code == "unavailable"
```

**Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_ratelimit.py -v`
Expected: FAIL (RateLimiter/RateLimitError missing).

**Step 3: Implement** — append to `ratelimit.py`:

```python
from datetime import datetime
from zoneinfo import ZoneInfo

import redis as redis_lib


class RateLimitError(Exception):
    """A request was refused. ``code`` is one of daily_limit |
    concurrency_limit | unavailable; ``retry_after`` is seconds for the
    HTTP Retry-After header; ``message`` is the user-facing Italian text."""

    def __init__(self, code: str, retry_after: int, message: str):
        super().__init__(code)
        self.code = code
        self.retry_after = retry_after
        self.message = message


_MESSAGES = {
    "daily_limit": "Hai raggiunto il limite di richieste giornaliere per questa demo. Riprova domani.",
    "concurrency_limit": "Hai già una richiesta in corso. Attendi che finisca prima di inviarne un'altra.",
    "unavailable": "Servizio temporaneamente non disponibile. Riprova tra qualche istante.",
}


@dataclass
class Lease:
    """The acquired concurrency slot to release when the stream ends."""

    conc_keys: tuple[str, ...]


class RateLimiter:
    def __init__(self, redis_client, *, concurrent: int, daily: int, tz: str):
        self.redis = redis_client
        self.concurrent = concurrent
        self.daily = daily
        self.tz = ZoneInfo(tz)

    def _now(self) -> datetime:
        return datetime.now(self.tz)

    def _today(self) -> str:
        return self._now().strftime("%Y-%m-%d")

    def _seconds_to_midnight(self) -> int:
        now = self._now()
        midnight = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return max(1, int((midnight - now).total_seconds()))

    def acquire(self, ip: str, lid: str) -> Lease:
        day = self._today()
        daily_keys = (f"daily:ip:{ip}:{day}", f"daily:cookie:{lid}:{day}")
        conc_keys = (f"conc:ip:{ip}", f"conc:cookie:{lid}")
        try:
            # 1. daily check (read-only): block before spending anything.
            for key in daily_keys:
                if int(self.redis.get(key) or 0) >= self.daily:
                    raise RateLimitError(
                        "daily_limit", self._seconds_to_midnight(),
                        _MESSAGES["daily_limit"],
                    )
            # 2. concurrency acquire (INCR then rollback if over).
            acquired: list[str] = []
            for key in conc_keys:
                n = self.redis.incr(key)
                self.redis.expire(key, CONC_TTL_S)
                acquired.append(key)
                if n > self.concurrent:
                    for k in acquired:
                        self.redis.decr(k)
                    raise RateLimitError(
                        "concurrency_limit", 5, _MESSAGES["concurrency_limit"],
                    )
            # 3. daily increment (admission); set TTL when first created.
            ttl = self._seconds_to_midnight()
            for key in daily_keys:
                if self.redis.incr(key) == 1:
                    self.redis.expire(key, ttl)
        except RateLimitError:
            raise
        except redis_lib.RedisError as exc:
            logger.error("rate limiter Redis error, failing closed: %s", exc)
            raise RateLimitError("unavailable", 5, _MESSAGES["unavailable"]) from exc
        except Exception as exc:  # the Boom() test path + any client lib quirk
            logger.error("rate limiter error, failing closed: %s", exc)
            raise RateLimitError("unavailable", 5, _MESSAGES["unavailable"]) from exc
        return Lease(conc_keys=conc_keys)

    def release(self, lease: Lease) -> None:
        for key in lease.conc_keys:
            try:
                self.redis.decr(key)
            except Exception:  # release must never raise into the stream
                logger.warning("rate limiter could not release %s", key, exc_info=True)
```

Add `timedelta` to the datetime import: `from datetime import datetime, timedelta`.

**Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_ratelimit.py -v`
Expected: PASS (all green)

**Step 5: Commit**

```bash
git add legger/api/ratelimit.py tests/test_ratelimit.py
git commit -m "feat: RateLimiter acquire/release with daily + concurrency"
```

---

### Task 5: Build the limiter in the app lifespan

**Files:**
- Modify: `backend/legger/api/app.py`
- Modify: `backend/legger/api/ratelimit.py` (factory)
- Test: `backend/tests/test_api_app.py`

**Step 1: Write the failing test**

```python
from legger.api.app import create_app
from legger.settings import Settings


def test_rate_limiter_absent_when_disabled():
    app = create_app(Settings(_env_file=None, rate_limit_enabled=False))
    with app.router.lifespan_context(app):
        assert app.state.rate_limiter is None


def test_rate_limiter_present_when_enabled(monkeypatch):
    import fakeredis
    import legger.api.ratelimit as rl_mod
    monkeypatch.setattr(
        rl_mod, "build_redis", lambda url: fakeredis.FakeStrictRedis(decode_responses=True)
    )
    app = create_app(Settings(_env_file=None, rate_limit_enabled=True))
    with app.router.lifespan_context(app):
        assert app.state.rate_limiter is not None
```

**Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_api_app.py -k rate_limiter -v`
Expected: FAIL (`app.state.rate_limiter` missing / `build_redis` missing).

**Step 3: Implement**

In `ratelimit.py` add a factory the lifespan calls (monkeypatchable seam):

```python
def build_redis(url: str):
    return redis_lib.from_url(url, decode_responses=True)


def build_rate_limiter(settings) -> "RateLimiter | None":
    if not settings.rate_limit_enabled:
        return None
    return RateLimiter(
        build_redis(settings.redis_url),
        concurrent=settings.rate_limit_per_user_concurrent,
        daily=settings.rate_limit_per_user_daily,
        tz=settings.rate_limit_tz,
    )
```

In `app.py` lifespan, after `app.state.anthropic = ...`:

```python
        from legger.api.ratelimit import build_rate_limiter
        app.state.rate_limiter = build_rate_limiter(settings)
```

(Import at module top instead, alongside the other `legger.api.*` imports, if preferred — but a local import keeps the seam patchable per test.)

**Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_api_app.py -k rate_limiter -v`
Expected: PASS

**Step 5: Commit**

```bash
git add legger/api/app.py legger/api/ratelimit.py tests/test_api_app.py
git commit -m "feat: build rate limiter in app lifespan"
```

---

### Task 6: Enforce in the `/chat` handler (429, cookie, release)

**Files:**
- Modify: `backend/legger/api/chat.py` (handler at ~`@router.post("/chat")`)
- Test: `backend/tests/test_api_chat.py`

**Step 1: Write the failing tests** (reuse the file's existing app/monkeypatch fixtures; add a fakeredis limiter onto `app.state`).

```python
def _enable_limiter(app, concurrent=2, daily=2):
    import fakeredis
    from legger.api.ratelimit import RateLimiter
    app.state.rate_limiter = RateLimiter(
        fakeredis.FakeStrictRedis(decode_responses=True),
        concurrent=concurrent, daily=daily, tz="Europe/Rome",
    )


def test_chat_sets_lid_cookie(client_with_stubs):
    client, app = client_with_stubs
    _enable_limiter(app)
    r = client.post("/chat", json={"messages": [{"role": "user", "content": "ciao"}]})
    assert r.status_code == 200
    assert "lid" in r.cookies


def test_chat_daily_limit_returns_429(client_with_stubs):
    client, app = client_with_stubs
    _enable_limiter(app, daily=1)
    ok = client.post("/chat", json={"messages": [{"role": "user", "content": "a"}]})
    assert ok.status_code == 200
    blocked = client.post("/chat", json={"messages": [{"role": "user", "content": "b"}]})
    assert blocked.status_code == 429
    assert blocked.json()["code"] == "daily_limit"
    assert "Retry-After" in blocked.headers


def test_chat_disabled_limiter_is_noop(client_with_stubs):
    client, app = client_with_stubs
    app.state.rate_limiter = None
    r = client.post("/chat", json={"messages": [{"role": "user", "content": "ok"}]})
    assert r.status_code == 200
```

> Note: if `test_api_chat.py` does not already expose a `client_with_stubs`
> fixture returning `(TestClient, app)`, add one that mirrors the existing
> per-test setup (stub `legger.api.app.get_embedder`, monkeypatch
> `legger.api.chat.retrieve`/`stream_answer`) and yields both the client and
> its `app`. TestClient persists cookies across calls, so the daily counter
> sees the same `lid` on the second request. Concurrency cannot be exercised
> with the synchronous TestClient (each request completes before the next);
> concurrency is covered in `test_ratelimit.py`.

**Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_api_chat.py -k "limit or cookie or noop" -v`
Expected: FAIL (no cookie / 200 instead of 429).

**Step 3: Implement the handler** — replace the body of `chat()`:

```python
from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse

from legger.api.ratelimit import RateLimitError, ensure_lid, client_ip, COOKIE_NAME


def _cookie_kwargs(request: Request) -> dict:
    secure = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    return dict(httponly=True, samesite="lax", secure=secure, max_age=60 * 60 * 24 * 365)


@router.post("/chat")
def chat(payload: ChatRequest, request: Request) -> Response:
    """Stream the grounded answer for a conversation as SSE (rate-limited)."""
    messages: list[Message] = [{"role": m.role, "content": m.content} for m in payload.messages]
    limiter = getattr(request.app.state, "rate_limiter", None)

    if limiter is None:
        return StreamingResponse(
            _event_stream(messages, request.app, payload.config),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    lid, lid_is_new = ensure_lid(request)
    ip = client_ip(request)
    try:
        lease = limiter.acquire(ip, lid)
    except RateLimitError as exc:
        resp = JSONResponse(status_code=429, content={"code": exc.code, "message": exc.message})
        resp.headers["Retry-After"] = str(exc.retry_after)
        if lid_is_new:
            resp.set_cookie(COOKIE_NAME, lid, **_cookie_kwargs(request))
        return resp

    def _streamer():
        try:
            yield from _event_stream(messages, request.app, payload.config)
        finally:
            limiter.release(lease)

    resp = StreamingResponse(
        _streamer(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
    if lid_is_new:
        resp.set_cookie(COOKIE_NAME, lid, **_cookie_kwargs(request))
    return resp
```

Update imports at the top of `chat.py`: ensure `Request`, `Response`, `JSONResponse`, `StreamingResponse` are imported (some already are). Change the return annotation import to `from starlette.responses import Response` if needed.

**Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_api_chat.py -v`
Expected: PASS (new tests + all pre-existing chat tests still green)

**Step 5: Commit**

```bash
git add legger/api/chat.py tests/test_api_chat.py
git commit -m "feat: enforce rate limits on POST /chat (429 + lid cookie + slot release)"
```

---

### Task 7: Frontend — handle 429 in `streamChat`

**Files:**
- Modify: `frontend/lib/sse.ts` (the `if (!response.ok ...)` branch, ~line 146)
- Test: `frontend/lib/sse.test.ts` (create if absent; otherwise add to the existing test for sse)

⚠️ Per `frontend/AGENTS.md`, this is a non-standard Next.js build. This change is plain fetch/TS though — no framework APIs. Before editing, skim `node_modules/next/dist/docs/` only if anything framework-specific surfaces.

**Step 1: Write the failing test** (vitest):

```ts
import { describe, expect, it, vi } from "vitest";
import { streamChat } from "./sse";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("streamChat 429 handling", () => {
  it("shows the daily-limit message", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      jsonResponse(429, { code: "daily_limit", message: "ignored" }),
    ));
    const onError = vi.fn();
    await streamChat([{ role: "user", content: "x" }], { onError });
    expect(onError).toHaveBeenCalledWith(
      expect.stringContaining("limite di richieste giornaliere"),
    );
  });

  it("shows the concurrency message", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      jsonResponse(429, { code: "concurrency_limit", message: "ignored" }),
    ));
    const onError = vi.fn();
    await streamChat([{ role: "user", content: "x" }], { onError });
    expect(onError).toHaveBeenCalledWith(expect.stringContaining("richiesta in corso"));
  });
});
```

**Step 2: Run to verify it fails**

Run (from `frontend/`): `npm test -- sse`
Expected: FAIL (generic network message, not the limit text). If `node_modules` is missing in the worktree, run `npm install` first.

**Step 3: Implement** — in `frontend/lib/sse.ts`, add a constant map and a branch before the existing `if (!response.ok ...)`:

```ts
const RATE_LIMIT_MESSAGES: Record<string, string> = {
  daily_limit:
    "Hai raggiunto il limite di richieste giornaliere per questa demo. Riprova domani.",
  concurrency_limit:
    "Hai già una richiesta in corso. Attendi che finisca prima di inviarne un'altra.",
  unavailable:
    "Servizio temporaneamente non disponibile. Riprova tra qualche istante.",
};

// ... inside streamChat, right after the fetch try/catch:
  if (response.status === 429) {
    const body = await response.json().catch(() => null);
    const message =
      RATE_LIMIT_MESSAGES[body?.code as string] ?? NETWORK_ERROR_MESSAGE;
    handlers.onError?.(message);
    return;
  }
```

**Step 4: Run to verify it passes**

Run: `npm test -- sse`
Expected: PASS

**Step 5: Commit**

```bash
git add frontend/lib/sse.ts frontend/lib/sse.test.ts
git commit -m "feat: surface /chat 429 rate-limit messages in the UI"
```

---

### Task 8: Infrastructure (Redis + env example)

**Files:**
- Modify: `docker-compose.yml` (dev)
- Modify: `docker-compose.prod.yml` (prod, + backend env)
- Modify: `.env.example`

**Step 1: Add Redis to dev compose** (`docker-compose.yml` services):

```yaml
  redis:
    image: redis:7-alpine
    command: ["redis-server", "--save", "", "--maxmemory", "64mb", "--maxmemory-policy", "allkeys-lru"]
    ports:
      - "6379:6379"
```

**Step 2: Add Redis to prod compose** (`docker-compose.prod.yml`): same service (no published port — internal only), and add to the backend service environment:

```yaml
      RATE_LIMIT_ENABLED: ${RATE_LIMIT_ENABLED:-true}
      REDIS_URL: ${REDIS_URL:-redis://redis:6379}
      RATE_LIMIT_PER_USER_CONCURRENT: ${RATE_LIMIT_PER_USER_CONCURRENT:-2}
      RATE_LIMIT_PER_USER_DAILY: ${RATE_LIMIT_PER_USER_DAILY:-30}
```

Add `redis` to the backend service `depends_on`.

**Step 3: Document in `.env.example`** — append a block:

```sh
# --- rate limiting on /chat (per-user IP + cookie; see docs/plans/...-design.md) ---
#RATE_LIMIT_ENABLED=true
#REDIS_URL=redis://redis:6379
#RATE_LIMIT_PER_USER_CONCURRENT=2
#RATE_LIMIT_PER_USER_DAILY=30
#RATE_LIMIT_TZ=Europe/Rome
```

**Step 4: Validate compose**

Run: `docker compose -f docker-compose.yml config -q && docker compose -f docker-compose.prod.yml config -q`
Expected: no errors (valid YAML/interpolation).

**Step 5: Commit**

```bash
git add docker-compose.yml docker-compose.prod.yml .env.example
git commit -m "build: add redis service + rate-limit env wiring"
```

---

### Task 9: README — "Demo & rate limiting" section

**Files:**
- Modify: `README.md`

**Step 1:** Add a section (after "Deployment") documenting: the demo link placeholder, that `/chat` is rate-limited per user (IP + cookie) with daily + concurrency caps, the env vars, fail-closed behavior, and that it is off by default locally. Keep it concise.

**Step 2: Commit**

```bash
git add README.md
git commit -m "docs: document demo rate limiting"
```

---

### Task 10: Full verification

**Step 1: Backend suite**

Run: `uv run pytest -q`
Expected: previous baseline (706 passed, 1 skipped) + the new tests, 0 failures.

**Step 2: Lint**

Run: `uv run ruff check legger tests && uv run ruff format --check legger tests`
Expected: clean (fix + re-run if needed).

**Step 3: Frontend**

Run (from `frontend/`): `npm test && npm run lint`
Expected: pass.

**Step 4:** Use superpowers:requesting-code-review, then superpowers:finishing-a-development-branch to merge `feature/rate-limiting` into `main` and push.
