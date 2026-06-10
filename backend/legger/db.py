"""Postgres schema (SQLAlchemy Core) and engine factory.

The ``acts`` table is the Postgres mirror of what Qdrant knows per act: the
payload fields shared with chunks (act_ref, act_type, title, collection,
vigenza, file_path) plus the ActRef extras (number, year, date_pub, source).
``ingestion_runs`` / ``ingestion_progress`` track bootstrap and delta runs (D2/D3).

Plain Core, no ORM: D2/D3 issue bulk upserts and the API (F1) does simple
selects, so sessions would be dead weight.
"""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    Connection,
    Date,
    Engine,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    create_engine,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.dialects.postgresql import insert as pg_insert

from legger.settings import Settings

metadata = MetaData()

acts = Table(
    "acts",
    metadata,
    Column("act_ref", Text, primary_key=True),
    Column("act_type", Text, nullable=False),
    Column("title", Text),
    Column("collection", Text, nullable=False),
    Column("vigenza", Text, nullable=False),
    Column("file_path", Text, nullable=False),
    Column("last_commit_sha", Text),
    Column("last_updated", TIMESTAMP(timezone=True)),
    Column("number", Text),
    Column("year", Integer),
    Column("date_pub", Date),
    Column("source", Text),  # ActRef derivation source: header | urn | filename
    CheckConstraint(
        "vigenza IN ('vigente', 'abrogato', 'decaduto')", name="ck_acts_vigenza"
    ),
    Index("ix_acts_collection", "collection"),
    Index("ix_acts_vigenza", "vigenza"),
)

ingestion_runs = Table(
    "ingestion_runs",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("started_at", TIMESTAMP(timezone=True)),
    Column("finished_at", TIMESTAMP(timezone=True)),
    Column("kind", Text),
    Column("commit_from", Text),
    Column("commit_to", Text),
    Column("files_processed", Integer, nullable=False, server_default=text("0")),
    Column("files_skipped", Integer, nullable=False, server_default=text("0")),
    Column("errors", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    Column("status", Text),
    CheckConstraint("kind IN ('bootstrap', 'delta')", name="ck_ingestion_runs_kind"),
    CheckConstraint(
        "status IN ('running', 'completed', 'failed')", name="ck_ingestion_runs_status"
    ),
)

ingestion_progress = Table(
    "ingestion_progress",
    metadata,
    Column("file_path", Text, primary_key=True),
    Column("commit_sha", Text, nullable=False),
    Column("act_ref", Text),
    Column("indexed_at", TIMESTAMP(timezone=True), nullable=False),
)


def get_engine(settings: Settings | None = None) -> Engine:
    """Pooled engine for ``settings.database_url`` (SQLAlchemy 2 future-style)."""
    settings = settings or Settings()
    return create_engine(settings.database_url, pool_pre_ping=True)


def _execute(bind: Engine | Connection, stmt) -> None:
    """Run *stmt* in its own transaction (Engine) or on the caller's (Connection)."""
    if isinstance(bind, Engine):
        with bind.begin() as conn:
            conn.execute(stmt)
    else:
        bind.execute(stmt)


def upsert_act(
    bind: Engine | Connection,
    *,
    act_ref: str,
    act_type: str,
    collection: str,
    vigenza: str,
    file_path: str,
    title: str | None = None,
    last_commit_sha: str | None = None,
    last_updated: datetime | None = None,
    number: str | None = None,
    year: int | None = None,
    date_pub: date | str | None = None,
    source: str | None = None,
) -> None:
    """INSERT the act or, on act_ref conflict, UPDATE every other column.

    .. warning::
        Full-row replace: omitted optional fields overwrite existing values
        with NULL. Not for partial updates — use :func:`set_vigenza` for
        vigenza flips.

    ``last_updated`` defaults to ``now()`` server-side when omitted.
    ``date_pub`` also accepts an ISO date string (``"2026-01-01"``) for
    convenience; psycopg adapts it to a ``date``.

    Note: for batching, ``pg_insert(acts).values([...])`` accepts a list of
    row dicts if D2 needs it.
    """
    values: dict[str, object] = {
        "act_ref": act_ref,
        "act_type": act_type,
        "title": title,
        "collection": collection,
        "vigenza": vigenza,
        "file_path": file_path,
        "last_commit_sha": last_commit_sha,
        "last_updated": last_updated if last_updated is not None else func.now(),
        "number": number,
        "year": year,
        "date_pub": date_pub,
        "source": source,
    }
    stmt = pg_insert(acts).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[acts.c.act_ref],
        set_={name: stmt.excluded[name] for name in values if name != "act_ref"},
    )
    _execute(bind, stmt)


def set_vigenza(
    bind: Engine | Connection,
    act_ref: str,
    vigenza: str,
    *,
    last_commit_sha: str | None = None,
) -> int:
    """Partial UPDATE of an act's vigenza (and optionally last_commit_sha).

    Unlike :func:`upsert_act` (full-row replace), this touches only
    ``vigenza``, ``last_updated`` (set to ``now()``) and — when given —
    ``last_commit_sha``; every other column is left intact.

    Returns the number of rows updated (0 if *act_ref* is unknown), so D3
    can detect acts missing from Postgres.
    """
    values: dict[str, object] = {"vigenza": vigenza, "last_updated": func.now()}
    if last_commit_sha is not None:
        values["last_commit_sha"] = last_commit_sha
    stmt = acts.update().where(acts.c.act_ref == act_ref).values(**values)
    if isinstance(bind, Engine):
        with bind.begin() as conn:
            return conn.execute(stmt).rowcount
    return bind.execute(stmt).rowcount


def upsert_progress(
    bind: Engine | Connection,
    *,
    file_path: str,
    commit_sha: str,
    act_ref: str | None = None,
    indexed_at: datetime | None = None,
) -> None:
    """INSERT the progress row or, on file_path conflict, UPDATE the other columns.

    ``indexed_at`` defaults to ``now()`` server-side when omitted.
    """
    values: dict[str, object] = {
        "file_path": file_path,
        "commit_sha": commit_sha,
        "act_ref": act_ref,
        "indexed_at": indexed_at if indexed_at is not None else func.now(),
    }
    stmt = pg_insert(ingestion_progress).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[ingestion_progress.c.file_path],
        set_={name: stmt.excluded[name] for name in values if name != "file_path"},
    )
    _execute(bind, stmt)
