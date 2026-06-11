"""`legger feedback report`: the 👍/👎 picture from ``message_feedback``.

Three plain-text sections:

1. totals — rows, 👍/👎 counts, overall 👍-rate;
2. breakdown by EFFECTIVE config combination (answer model/effort + QU
   model/effort, as stored from the ``done`` event), with per-combination
   👍-rate and n — the whole point of storing the config on each row;
3. the most recent negative feedbacks (created_at, question truncated,
   reason, compact config) — the reading list for answer-quality triage.

Read-only: two SELECTs over the table, aggregation in Python (the table is
beta-volume, thousands of rows at most). The engine comes from
:func:`legger.db.get_engine`, same as everything else.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from legger.db import message_feedback

if TYPE_CHECKING:
    from sqlalchemy import Engine

#: How many recent negative feedbacks the report lists.
NEGATIVES_LIMIT = 20

#: Question truncation width in the negatives section.
QUESTION_WIDTH = 120


def compact_config(config: dict | None) -> str:
    """One-line key for a stored config dict, e.g. ``sonnet-4-6/high + qu:haiku-4-5``.

    ``claude-`` prefixes are stripped for width; a missing/empty/all-None
    config (defaults, or rows from before config tracking) reads ``default``.
    """
    config = config or {}

    def part(model: object, effort: object) -> str | None:
        if not isinstance(model, str) or not model:
            return None
        name = model.removeprefix("claude-")
        return f"{name}/{effort}" if isinstance(effort, str) and effort else name

    answer = part(config.get("answer_model"), config.get("answer_effort"))
    qu = part(config.get("qu_model"), config.get("qu_effort"))
    if answer is None and qu is None:
        return "default"
    return f"{answer or 'default'} + qu:{qu or 'default'}"


def _truncate(text: str, width: int) -> str:
    flat = " ".join(text.split())  # newlines would break the table layout
    flat = "".join(ch for ch in flat if ch >= " ")  # no terminal escape sequences
    return flat if len(flat) <= width else flat[: width - 1] + "…"


def _rate(up: int, total: int) -> str:
    return f"{up / total:6.1%}" if total else "     —"


def build_report(engine: Engine) -> str:
    """The full plain-text report (printed verbatim by the CLI)."""
    with engine.connect() as conn:
        rows = conn.execute(select(message_feedback.c.rating, message_feedback.c.config)).all()
        negatives = conn.execute(
            select(
                message_feedback.c.created_at,
                message_feedback.c.question,
                message_feedback.c.reason,
                message_feedback.c.config,
            )
            .where(message_feedback.c.rating == -1)
            .order_by(message_feedback.c.created_at.desc())
            .limit(NEGATIVES_LIMIT)
        ).all()

    lines: list[str] = []
    total = len(rows)
    up = sum(1 for r in rows if r.rating == 1)
    down = total - up

    lines.append("FEEDBACK REPORT")
    lines.append("=" * 79)
    lines.append(f"Totale: {total}  (👍 {up} / 👎 {down})  —  👍-rate: {_rate(up, total).strip()}")

    # --- breakdown by config combination ---------------------------------
    lines.append("")
    lines.append("Per configurazione (answer model/effort + qu model/effort)")
    lines.append("-" * 79)
    groups: dict[str, list[int]] = {}  # key -> [up, n]
    for row in rows:
        key = compact_config(row.config)
        bucket = groups.setdefault(key, [0, 0])
        bucket[0] += row.rating == 1
        bucket[1] += 1
    if not groups:
        lines.append("(nessun feedback)")
    else:
        width = max(len(key) for key in groups)
        lines.append(f"{'config':<{width}}  {'👍-rate':>7}  {'n':>5}")
        for key, (group_up, n) in sorted(groups.items(), key=lambda kv: -kv[1][1]):
            lines.append(f"{key:<{width}}  {_rate(group_up, n)}  {n:>5}")

    # --- recent negatives -------------------------------------------------
    lines.append("")
    lines.append(f"Ultimi {NEGATIVES_LIMIT} feedback negativi")
    lines.append("-" * 79)
    if not negatives:
        lines.append("(nessun feedback negativo)")
    for row in negatives:
        created = row.created_at.strftime("%Y-%m-%d %H:%M") if row.created_at else "?"
        lines.append(f"{created}  {_truncate(row.question, QUESTION_WIDTH)}")
        lines.append(f"    motivo: {_truncate(row.reason, QUESTION_WIDTH) if row.reason else '—'}")
        lines.append(f"    config: {compact_config(row.config)}")

    return "\n".join(lines)
