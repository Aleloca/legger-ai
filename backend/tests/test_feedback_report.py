"""Tests for `legger feedback report`: formatting (no DB) + seeded run (-m db)."""

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine

from legger import cli
from legger.db import get_engine, insert_feedback, message_feedback
from legger.feedback_report import _truncate, build_report, compact_config

# ---------------------------------------------------------------------------
# compact_config (no DB)
# ---------------------------------------------------------------------------


def test_compact_config_full_combination() -> None:
    assert (
        compact_config(
            {
                "answer_model": "claude-opus-4-8",
                "answer_effort": "max",
                "qu_model": "claude-sonnet-4-6",
                "qu_effort": "low",
            }
        )
        == "opus-4-8/max + qu:sonnet-4-6/low"
    )


def test_compact_config_defaults() -> None:
    assert compact_config({}) == "default"
    assert compact_config(None) == "default"
    assert (
        compact_config(
            {"answer_model": None, "answer_effort": None, "qu_model": None, "qu_effort": None}
        )
        == "default"
    )


def test_compact_config_partial() -> None:
    assert (
        compact_config({"answer_model": "claude-haiku-4-5", "answer_effort": None})
        == "haiku-4-5 + qu:default"
    )


def test_truncate_strips_control_chars() -> None:
    # Terminal escape sequences in question/reason must never reach the CLI.
    assert _truncate("ciao\x1b[31mrosso\x07 fine", 120) == "ciao[31mrosso fine"


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------


def test_feedback_without_subcommand_exits_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["legger", "feedback"])
    with pytest.raises(SystemExit) as exc_info:
        cli.main()
    assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# Integration against the local Postgres (run with: pytest -m db)
# ---------------------------------------------------------------------------

_MARK = "test:report:"


@pytest.fixture
def seeded_engine() -> Iterator[Engine]:
    eng = get_engine()
    rows = [
        # 3x sonnet-default: 2 up, 1 down
        {"rating": 1, "question": f"{_MARK}q1", "answer": "a", "config": {}},
        {"rating": 1, "question": f"{_MARK}q2", "answer": "a", "config": {}},
        {
            "rating": -1,
            "question": f"{_MARK}q3 " + "lunga " * 40,  # forces question truncation
            "answer": "a",
            "reason": "Risposta generica, nessuna citazione utile.",
            "config": {},
        },
        # 2x opus/max: 1 up, 1 down (no reason)
        {
            "rating": 1,
            "question": f"{_MARK}q4",
            "answer": "a",
            "config": {
                "answer_model": "claude-opus-4-8",
                "answer_effort": "max",
                "qu_model": "claude-haiku-4-5",
                "qu_effort": None,
            },
        },
        {
            "rating": -1,
            "question": f"{_MARK}q5",
            "answer": "a",
            "config": {
                "answer_model": "claude-opus-4-8",
                "answer_effort": "max",
                "qu_model": "claude-haiku-4-5",
                "qu_effort": None,
            },
        },
    ]
    for row in rows:
        insert_feedback(eng, **row)
    yield eng
    with eng.begin() as conn:
        conn.execute(message_feedback.delete().where(message_feedback.c.question.like(f"{_MARK}%")))
    eng.dispose()


@pytest.mark.db
def test_report_sections_with_seeded_rows(seeded_engine: Engine) -> None:
    report = build_report(seeded_engine)

    # (a) totals: at least the seeded rows are counted, rate is rendered.
    assert "FEEDBACK REPORT" in report
    assert "👍-rate" in report

    # (b) breakdown lists both seeded config combinations.
    assert "default" in report
    assert "opus-4-8/max + qu:haiku-4-5" in report

    # (c) negatives: created_at + truncated question + reason + compact config.
    assert f"{_MARK}q5" in report
    assert "Risposta generica, nessuna citazione utile." in report
    assert "motivo: —" in report  # the reason-less 👎
    # The long question is truncated with an ellipsis, never dumped whole.
    truncated_line = next(line for line in report.splitlines() if f"{_MARK}q3" in line)
    assert truncated_line.endswith("…")
    assert "lunga " * 40 not in report


@pytest.mark.db
def test_cli_feedback_report_prints(
    seeded_engine: Engine, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setattr("sys.argv", ["legger", "feedback", "report"])
    cli.main()
    out = capsys.readouterr().out
    assert "FEEDBACK REPORT" in out
    assert "opus-4-8/max + qu:haiku-4-5" in out
