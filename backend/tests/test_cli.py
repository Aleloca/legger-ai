"""Smoke tests for the legger CLI."""

import pytest

from legger import cli


def test_main_exists() -> None:
    assert callable(cli.main)


def test_help_exits_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["legger", "--help"])
    with pytest.raises(SystemExit) as exc_info:
        cli.main()
    assert exc_info.value.code == 0


@pytest.mark.parametrize("command", ["ingest", "eval", "chat"])
def test_stub_commands_exit_nonzero(monkeypatch: pytest.MonkeyPatch, command: str) -> None:
    monkeypatch.setattr("sys.argv", ["legger", command])
    with pytest.raises(SystemExit) as exc_info:
        cli.main()
    assert exc_info.value.code == 1
