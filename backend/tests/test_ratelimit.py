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
