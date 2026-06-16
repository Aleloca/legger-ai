import uuid
from types import SimpleNamespace

import fakeredis
import pytest

from legger.api.ratelimit import (
    COOKIE_NAME,
    RateLimiter,
    RateLimitError,
    client_ip,
    ensure_lid,
)


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


def _limiter(concurrent=2, daily=3):
    return RateLimiter(
        fakeredis.FakeStrictRedis(decode_responses=True),
        concurrent=concurrent,
        daily=daily,
        tz="Europe/Rome",
    )


def test_acquire_release_allows_repeat_within_limits():
    rl = _limiter()
    lease = rl.acquire("1.1.1.1", "lidA")  # daily 1/3, conc 1/2
    rl.release(lease)
    lease = rl.acquire("1.1.1.1", "lidA")  # daily 2/3, conc 1/2 again
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
    rl.acquire("1.1.1.1", "lidA")  # daily 1/2
    with pytest.raises(RateLimitError):
        rl.acquire("1.1.1.1", "lidA")  # blocked on conc, daily untouched
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
    rl.release(rl.acquire("1.1.1.1", "lidA"))  # ip and lidA both at 1/1
    with pytest.raises(RateLimitError) as ei:  # same ip, fresh cookie
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
