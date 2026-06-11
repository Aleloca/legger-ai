"""Operational alerting via Telegram (Task D4, design §8 risk #1).

The ingestion pipeline is a cron job on a side project: nobody is watching
stdout. This module pushes the few events that need a human onto a Telegram
chat — pull/diff refused, delta completed with per-file errors, delta failed,
and upstream staleness (no corpus commit for >7 days, the early warning that
the single-maintainer upstream repo may have been abandoned).

Setup (one-time)
================
1. Create a bot: talk to ``@BotFather`` on Telegram, ``/newbot``, follow the
   prompts. BotFather replies with the bot token (``123456:ABC-...``) — put
   it in the repo-root ``.env`` as ``TELEGRAM_BOT_TOKEN``.
2. Discover your chat id: send any message to the new bot (or add it to a
   group and post there), then::

       curl https://api.telegram.org/bot<TOKEN>/getUpdates

   and read ``result[].message.chat.id`` from the JSON. Put that number in
   ``.env`` as ``TELEGRAM_CHAT_ID``.

With either variable unset every alert is a logged no-op returning ``False``
— the pipeline never depends on Telegram being configured or reachable, and
:func:`send_alert` NEVER raises.

Staleness dedup state
=====================
The upstream-staleness check runs after every delta AND from its own cron
(``legger ingest check-upstream``), so a stale upstream would otherwise spam
one alert per run. The dedup guard keeps a single tiny JSON file of
``{alert_key: last_alert_iso_timestamp}`` at
``$XDG_STATE_HOME/legger/alerts.json`` (default
``~/.local/state/legger/alerts.json``) and re-alerts at most once every 24
hours. The timestamp is recorded only on a SUCCESSFUL send, so a network
hiccup does not swallow the daily alert. Deleting the file is always safe
(worst case: one extra alert).
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from legger.settings import Settings

logger = logging.getLogger(__name__)

ALERT_PREFIX = "⚖️ legger.ai — "
SEND_TIMEOUT_S = 10.0
DEDUP_WINDOW = timedelta(hours=24)
_GIT_TIMEOUT_S = 30


def send_alert(message: str, *, settings: Settings) -> bool:
    """Push one alert message to the configured Telegram chat.

    Returns True only when Telegram acknowledged the message. Unset
    token/chat_id is a debug-logged no-op (False); network/API errors are
    warning-logged and swallowed (False). This function never raises: an
    alerting failure must never take down the ingestion run it reports on.
    """
    token = settings.telegram_bot_token
    chat_id = settings.telegram_chat_id
    if not token or not chat_id:
        logger.debug(
            "alert telegram non configurato (TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID): %s", message
        )
        return False
    try:
        response = httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": ALERT_PREFIX + message},
            timeout=SEND_TIMEOUT_S,
        )
        response.raise_for_status()
    except Exception:
        logger.warning("invio alert telegram fallito: %s", message, exc_info=True)
        return False
    logger.info("alert telegram inviato: %s", message)
    return True


# ---------------------------------------------------------------------------
# Upstream staleness (design §8 risk #1: upstream abandonment early warning)
# ---------------------------------------------------------------------------


def default_state_path() -> Path:
    """Dedup state file, honoring ``XDG_STATE_HOME`` (module docstring)."""
    base = os.environ.get("XDG_STATE_HOME")
    root = Path(base) if base else Path.home() / ".local" / "state"
    return root / "legger" / "alerts.json"


def _load_state(path: Path) -> dict[str, str]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _save_state(path: Path, state: dict[str, str]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    except OSError:
        logger.warning("impossibile scrivere lo stato alert %s", path, exc_info=True)


def _last_alert_at(state: dict[str, str], key: str) -> datetime | None:
    value = state.get(key)
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def last_upstream_commit(corpus_path: Path) -> datetime | None:
    """Committer date of the corpus clone's HEAD; None when unreadable.

    Never raises: an unreadable clone is logged and treated as "cannot
    tell" (the pull failure path already alerts on a broken checkout).
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(corpus_path), "log", "-1", "--format=%cI"],
            capture_output=True,
            text=True,
            check=True,
            timeout=_GIT_TIMEOUT_S,
        )
        return datetime.fromisoformat(proc.stdout.strip())
    except (subprocess.SubprocessError, OSError, ValueError):
        logger.warning("data ultimo commit upstream illeggibile in %s", corpus_path, exc_info=True)
        return None


def check_upstream_freshness(
    corpus_path: Path,
    max_days: int = 7,
    *,
    settings: Settings | None = None,
    state_path: Path | None = None,
) -> bool:
    """Alert when the corpus upstream has had no commit for over ``max_days``.

    Returns True when the upstream is STALE (the alert condition holds),
    False when it is fresh or its age cannot be determined. The alert itself
    is deduplicated to at most one per :data:`DEDUP_WINDOW` via the state
    file (module docstring), so this is safe to run after every delta and
    from a separate cron. Never raises.
    """
    last_commit = last_upstream_commit(corpus_path)
    if last_commit is None:
        return False
    now = datetime.now(UTC)
    age_days = (now - last_commit).total_seconds() / 86400
    if age_days <= max_days:
        logger.debug(
            "upstream fresco: ultimo commit %s (%.1f giorni)", last_commit.date(), age_days
        )
        return False

    state_path = state_path or default_state_path()
    state = _load_state(state_path)
    last_alert = _last_alert_at(state, "upstream_stale")
    if last_alert is not None and now - last_alert < DEDUP_WINDOW:
        logger.debug(
            "upstream stantio (%.0f giorni) ma alert gia' inviato alle %s; dedup 24h",
            age_days,
            last_alert.isoformat(),
        )
        return True
    message = (
        f"nessun commit upstream da {int(age_days)} giorni "
        f"(ultimo: {last_commit.date().isoformat()} in {corpus_path})"
    )
    if send_alert(message, settings=settings or Settings()):
        state["upstream_stale"] = now.isoformat()
        _save_state(state_path, state)
    return True
