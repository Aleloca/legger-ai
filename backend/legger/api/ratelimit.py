"""Per-user rate limiting for POST /chat (IP + anonymous cookie, Redis-backed).

Design: docs/plans/2026-06-16-rate-limiting-design.md. Disabled unless
Settings.rate_limit_enabled; when disabled the app never builds a RateLimiter
and the /chat handler skips all of this.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import redis as redis_lib

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
        midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
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
                        "daily_limit",
                        self._seconds_to_midnight(),
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
                        "concurrency_limit",
                        5,
                        _MESSAGES["concurrency_limit"],
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
