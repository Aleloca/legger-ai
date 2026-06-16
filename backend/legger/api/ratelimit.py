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
