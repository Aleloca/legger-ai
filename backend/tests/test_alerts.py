"""Tests for legger.alerts (Task D4): Telegram send, staleness check, CLI wiring."""

import json
import logging
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from legger import alerts, cli
from legger.ingestion.delta import DeltaReport, DeltaRefusedError
from legger.settings import Settings


def _settings(**overrides) -> Settings:
    defaults = {"telegram_bot_token": "123:ABC", "telegram_chat_id": "42"}
    return Settings(_env_file=None, **{**defaults, **overrides})


# ---------------------------------------------------------------------------
# send_alert
# ---------------------------------------------------------------------------


def test_send_alert_happy_path_url_and_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple] = []

    def fake_post(url: str, *, json: dict, timeout: float) -> httpx.Response:
        calls.append((url, json, timeout))
        return httpx.Response(200, request=httpx.Request("POST", url), json={"ok": True})

    monkeypatch.setattr(alerts.httpx, "post", fake_post)
    assert alerts.send_alert("pull fallito", settings=_settings()) is True
    assert len(calls) == 1
    url, payload, timeout = calls[0]
    assert url == "https://api.telegram.org/bot123:ABC/sendMessage"
    assert payload == {"chat_id": "42", "text": "⚖️ legger.ai — pull fallito"}
    assert timeout == alerts.SEND_TIMEOUT_S


def test_send_alert_message_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[str] = []

    def fake_post(url: str, *, json: dict, timeout: float) -> httpx.Response:
        sent.append(json["text"])
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(alerts.httpx, "post", fake_post)
    alerts.send_alert("ciao", settings=_settings())
    assert sent == ["⚖️ legger.ai — ciao"]
    assert sent[0].startswith(alerts.ALERT_PREFIX)


@pytest.mark.parametrize("missing", ["telegram_bot_token", "telegram_chat_id"])
def test_send_alert_unconfigured_is_noop(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, missing: str
) -> None:
    def explode(*args, **kwargs):
        raise AssertionError("httpx must not be called when telegram is unconfigured")

    monkeypatch.setattr(alerts.httpx, "post", explode)
    with caplog.at_level(logging.DEBUG, logger="legger.alerts"):
        assert alerts.send_alert("msg", settings=_settings(**{missing: ""})) is False
    assert any("non configurato" in record.message for record in caplog.records)


def test_send_alert_network_error_returns_false_and_logs(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def fake_post(url: str, *, json: dict, timeout: float) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(alerts.httpx, "post", fake_post)
    with caplog.at_level(logging.WARNING, logger="legger.alerts"):
        assert alerts.send_alert("msg", settings=_settings()) is False
    assert any(record.levelno == logging.WARNING for record in caplog.records)


def test_send_alert_http_error_status_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(url: str, *, json: dict, timeout: float) -> httpx.Response:
        return httpx.Response(401, request=httpx.Request("POST", url), json={"ok": False})

    monkeypatch.setattr(alerts.httpx, "post", fake_post)
    assert alerts.send_alert("msg", settings=_settings()) is False


def test_settings_telegram_defaults_empty() -> None:
    settings = Settings(_env_file=None)
    assert settings.telegram_bot_token == ""
    assert settings.telegram_chat_id == ""


# ---------------------------------------------------------------------------
# check_upstream_freshness
# ---------------------------------------------------------------------------


def _make_repo(path: Path, *, days_ago: int) -> None:
    """Git repo with one commit whose committer date is ``days_ago`` days old."""
    date = (datetime.now(UTC) - timedelta(days=days_ago)).isoformat()
    env = {
        **os.environ,
        "GIT_AUTHOR_DATE": date,
        "GIT_COMMITTER_DATE": date,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.org",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.org",
    }
    subprocess.run(["git", "init", "-q", str(path)], check=True, env=env)
    (path / "atto.md").write_text("# atto\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True, env=env)
    subprocess.run(
        ["git", "-C", str(path), "commit", "-q", "--no-gpg-sign", "-m", "c"], check=True, env=env
    )


@pytest.fixture
def sent_alerts(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    sent: list[str] = []

    def fake_send(message: str, *, settings) -> bool:
        sent.append(message)
        return True

    monkeypatch.setattr(alerts, "send_alert", fake_send)
    return sent


def test_check_upstream_fresh_no_alert(tmp_path: Path, sent_alerts: list[str]) -> None:
    repo = tmp_path / "corpus"
    _make_repo(repo, days_ago=1)
    state = tmp_path / "alerts.json"
    assert alerts.check_upstream_freshness(repo, settings=_settings(), state_path=state) is False
    assert sent_alerts == []
    assert not state.exists()


def test_check_upstream_stale_alerts_with_days(tmp_path: Path, sent_alerts: list[str]) -> None:
    repo = tmp_path / "corpus"
    _make_repo(repo, days_ago=30)
    state = tmp_path / "alerts.json"
    assert alerts.check_upstream_freshness(repo, settings=_settings(), state_path=state) is True
    assert len(sent_alerts) == 1
    assert "nessun commit upstream da 30 giorni" in sent_alerts[0]
    assert json.loads(state.read_text())["upstream_stale"]  # dedup timestamp recorded


def test_check_upstream_custom_max_days(tmp_path: Path, sent_alerts: list[str]) -> None:
    repo = tmp_path / "corpus"
    _make_repo(repo, days_ago=10)
    state = tmp_path / "alerts.json"
    assert (
        alerts.check_upstream_freshness(repo, 14, settings=_settings(), state_path=state) is False
    )
    assert sent_alerts == []


def test_check_upstream_dedup_within_24h(tmp_path: Path, sent_alerts: list[str]) -> None:
    repo = tmp_path / "corpus"
    _make_repo(repo, days_ago=30)
    state = tmp_path / "alerts.json"
    assert alerts.check_upstream_freshness(repo, settings=_settings(), state_path=state) is True
    assert alerts.check_upstream_freshness(repo, settings=_settings(), state_path=state) is True
    assert len(sent_alerts) == 1  # second call still reports stale but does not re-send


def test_check_upstream_dedup_expires_after_24h(tmp_path: Path, sent_alerts: list[str]) -> None:
    repo = tmp_path / "corpus"
    _make_repo(repo, days_ago=30)
    state = tmp_path / "alerts.json"
    stale_ts = (datetime.now(UTC) - timedelta(hours=25)).isoformat()
    state.write_text(json.dumps({"upstream_stale": stale_ts}), encoding="utf-8")
    assert alerts.check_upstream_freshness(repo, settings=_settings(), state_path=state) is True
    assert len(sent_alerts) == 1
    # the dedup timestamp moved forward
    assert json.loads(state.read_text())["upstream_stale"] > stale_ts


def test_check_upstream_failed_send_does_not_record_dedup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A network hiccup must not swallow the daily alert: no timestamp on False."""
    repo = tmp_path / "corpus"
    _make_repo(repo, days_ago=30)
    state = tmp_path / "alerts.json"
    monkeypatch.setattr(alerts, "send_alert", lambda message, *, settings: False)
    assert alerts.check_upstream_freshness(repo, settings=_settings(), state_path=state) is True
    assert not state.exists()


def test_check_upstream_not_a_repo_is_safe(tmp_path: Path, sent_alerts: list[str]) -> None:
    not_repo = tmp_path / "empty"
    not_repo.mkdir()
    assert (
        alerts.check_upstream_freshness(not_repo, settings=_settings(), state_path=tmp_path / "s")
        is False
    )
    assert sent_alerts == []


def test_check_upstream_corrupt_state_file_is_tolerated(
    tmp_path: Path, sent_alerts: list[str]
) -> None:
    repo = tmp_path / "corpus"
    _make_repo(repo, days_ago=30)
    state = tmp_path / "alerts.json"
    state.write_text("{not json", encoding="utf-8")
    assert alerts.check_upstream_freshness(repo, settings=_settings(), state_path=state) is True
    assert len(sent_alerts) == 1


def test_default_state_path_honors_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    assert alerts.default_state_path() == tmp_path / "xdg" / "legger" / "alerts.json"
    monkeypatch.delenv("XDG_STATE_HOME")
    assert (
        alerts.default_state_path() == Path.home() / ".local" / "state" / "legger" / "alerts.json"
    )


# ---------------------------------------------------------------------------
# CLI wiring (legger ingest delta / check-upstream)
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_alerts(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Mock the whole alerts surface the CLI touches; record the calls."""
    recorded: dict = {"sent": [], "freshness_calls": []}

    def fake_send(message: str, *, settings) -> bool:
        recorded["sent"].append(message)
        return True

    def fake_check(corpus_path, max_days=7, *, settings=None, state_path=None) -> bool:
        recorded["freshness_calls"].append((corpus_path, max_days))
        return False

    monkeypatch.setattr(alerts, "send_alert", fake_send)
    monkeypatch.setattr(alerts, "check_upstream_freshness", fake_check)
    return recorded


def _fake_delta(report: DeltaReport):
    def fake(**kwargs) -> DeltaReport:
        return report

    return fake


def test_cli_delta_refused_sends_alert(
    monkeypatch: pytest.MonkeyPatch, cli_alerts: dict, capsys: pytest.CaptureFixture
) -> None:
    import legger.ingestion.delta as delta_mod

    def refuse(**kwargs):
        raise DeltaRefusedError("git pull fallito in /corpus: not a repo")

    monkeypatch.setattr(delta_mod, "delta", refuse)
    monkeypatch.setattr("sys.argv", ["legger", "ingest", "delta"])
    with pytest.raises(SystemExit) as exc_info:
        cli.main()
    assert exc_info.value.code == 1
    assert cli_alerts["sent"] == ["delta rifiutata: git pull fallito in /corpus: not a repo"]
    assert cli_alerts["freshness_calls"] == []  # refused before any run: no inline check
    assert "Delta rifiutato" in capsys.readouterr().out


def test_cli_delta_with_errors_sends_alert(
    monkeypatch: pytest.MonkeyPatch, cli_alerts: dict
) -> None:
    import legger.ingestion.delta as delta_mod

    report = DeltaReport(
        status="completed",
        commit_from="a" * 40,
        commit_to="b" * 40,
        run_id=7,
        files_changed=5,
        files_indexed=3,
        errors=[
            {"file_path": "Codici/a.md", "error": "boom"},
            {"file_path": "Codici/b.md", "error": "boom"},
        ],
    )
    monkeypatch.setattr(delta_mod, "delta", _fake_delta(report))
    monkeypatch.setattr("sys.argv", ["legger", "ingest", "delta"])
    cli.main()
    assert cli_alerts["sent"] == ["delta completata con 2 errori su 5 file"]
    assert len(cli_alerts["freshness_calls"]) == 1  # staleness check runs inline


def test_cli_delta_clean_run_no_alert(monkeypatch: pytest.MonkeyPatch, cli_alerts: dict) -> None:
    import legger.ingestion.delta as delta_mod

    report = DeltaReport(
        status="completed",
        commit_from="a" * 40,
        commit_to="b" * 40,
        run_id=8,
        files_changed=3,
        files_indexed=3,
    )
    monkeypatch.setattr(delta_mod, "delta", _fake_delta(report))
    monkeypatch.setattr("sys.argv", ["legger", "ingest", "delta"])
    cli.main()
    assert cli_alerts["sent"] == []
    assert len(cli_alerts["freshness_calls"]) == 1  # the inline check still runs


def test_cli_delta_failed_sends_alert_with_note(
    monkeypatch: pytest.MonkeyPatch, cli_alerts: dict
) -> None:
    import legger.ingestion.delta as delta_mod

    report = DeltaReport(
        status="failed",
        commit_from="a" * 40,
        commit_to="b" * 40,
        run_id=9,
        files_changed=2,
        note="fatal: RuntimeError: qdrant down",
    )
    monkeypatch.setattr(delta_mod, "delta", _fake_delta(report))
    monkeypatch.setattr("sys.argv", ["legger", "ingest", "delta"])
    with pytest.raises(SystemExit) as exc_info:
        cli.main()
    assert exc_info.value.code == 1
    assert cli_alerts["sent"] == ["delta fallita: fatal: RuntimeError: qdrant down"]
    assert len(cli_alerts["freshness_calls"]) == 1  # checked even on a failed run


def test_cli_check_upstream_fresh_exits_zero(
    monkeypatch: pytest.MonkeyPatch, cli_alerts: dict, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setattr("sys.argv", ["legger", "ingest", "check-upstream", "--max-days", "14"])
    cli.main()
    assert cli_alerts["freshness_calls"] == [(Settings().corpus_path, 14)]
    assert "Upstream OK" in capsys.readouterr().out


def test_cli_check_upstream_stale_exits_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    calls: list[tuple] = []

    def fake_check(corpus_path, max_days=7, *, settings=None, state_path=None) -> bool:
        calls.append((corpus_path, max_days))
        return True

    monkeypatch.setattr(alerts, "check_upstream_freshness", fake_check)
    monkeypatch.setattr("sys.argv", ["legger", "ingest", "check-upstream"])
    with pytest.raises(SystemExit) as exc_info:
        cli.main()
    assert exc_info.value.code == 1
    assert calls == [(Settings().corpus_path, 7)]
    assert "Upstream stantio" in capsys.readouterr().out
