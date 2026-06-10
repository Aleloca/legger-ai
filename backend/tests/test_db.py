"""Tests for legger.db: schema sanity (no DB) + integration upserts (-m db)."""

from collections.abc import Iterator

import pytest
from sqlalchemy import CheckConstraint, Engine, select

from legger.db import (
    acts,
    get_engine,
    ingestion_progress,
    ingestion_runs,
    metadata,
    upsert_act,
    upsert_progress,
)
from legger.settings import Settings

# ---------------------------------------------------------------------------
# Schema sanity (no live DB)
# ---------------------------------------------------------------------------


def test_metadata_has_all_tables() -> None:
    assert set(metadata.tables) == {"acts", "ingestion_runs", "ingestion_progress"}


def test_acts_schema() -> None:
    assert [c.name for c in acts.primary_key.columns] == ["act_ref"]
    for name in ("act_type", "collection", "vigenza", "file_path"):
        assert not acts.c[name].nullable, name
    for name in ("title", "last_commit_sha", "number", "year", "date_pub", "source"):
        assert acts.c[name].nullable, name

    checks = [c for c in acts.constraints if isinstance(c, CheckConstraint)]
    assert any(c.name == "ck_acts_vigenza" for c in checks)
    assert {i.name for i in acts.indexes} == {"ix_acts_collection", "ix_acts_vigenza"}


def test_ingestion_runs_schema() -> None:
    assert [c.name for c in ingestion_runs.primary_key.columns] == ["id"]
    assert ingestion_runs.c.id.autoincrement is True
    for name in ("files_processed", "files_skipped", "errors"):
        assert ingestion_runs.c[name].server_default is not None, name
    check_names = {
        c.name for c in ingestion_runs.constraints if isinstance(c, CheckConstraint)
    }
    assert {"ck_ingestion_runs_kind", "ck_ingestion_runs_status"} <= check_names


def test_ingestion_progress_schema() -> None:
    assert [c.name for c in ingestion_progress.primary_key.columns] == ["file_path"]
    assert not ingestion_progress.c.commit_sha.nullable
    assert not ingestion_progress.c.indexed_at.nullable
    assert ingestion_progress.c.act_ref.nullable


def test_get_engine_uses_settings_url() -> None:
    settings = Settings(
        _env_file=None, database_url="postgresql+psycopg://u:p@db:5432/x"
    )
    engine = get_engine(settings)
    assert engine.url.render_as_string(hide_password=False).endswith("u:p@db:5432/x")
    assert engine.dialect.name == "postgresql"


# ---------------------------------------------------------------------------
# Integration against the local Postgres (run with: pytest -m db)
# ---------------------------------------------------------------------------

_ACT_REF = "test:db:act/2026/1"
_FILE_PATH = "Test/db/atto-di-prova.md"


@pytest.fixture
def engine() -> Iterator[Engine]:
    eng = get_engine()
    yield eng
    with eng.begin() as conn:
        conn.execute(acts.delete().where(acts.c.act_ref == _ACT_REF))
        conn.execute(
            ingestion_progress.delete().where(
                ingestion_progress.c.file_path == _FILE_PATH
            )
        )
    eng.dispose()


@pytest.mark.db
def test_upsert_act_insert_then_update(engine: Engine) -> None:
    upsert_act(
        engine,
        act_ref=_ACT_REF,
        act_type="legge",
        collection="Test",
        vigenza="vigente",
        file_path=_FILE_PATH,
        title="Atto di prova",
        number="1",
        year=2026,
        date_pub="2026-01-01",
        source="header",
    )
    with engine.connect() as conn:
        row = conn.execute(select(acts).where(acts.c.act_ref == _ACT_REF)).one()
    assert row.title == "Atto di prova"
    assert row.vigenza == "vigente"
    assert row.last_updated is not None  # server-side now() default

    upsert_act(
        engine,
        act_ref=_ACT_REF,
        act_type="legge",
        collection="Test",
        vigenza="abrogato",
        file_path=_FILE_PATH,
        title="Atto di prova (abrogato)",
        last_commit_sha="deadbeef",
    )
    with engine.connect() as conn:
        rows = conn.execute(select(acts).where(acts.c.act_ref == _ACT_REF)).all()
    assert len(rows) == 1  # updated in place, not duplicated
    assert rows[0].vigenza == "abrogato"
    assert rows[0].title == "Atto di prova (abrogato)"
    assert rows[0].last_commit_sha == "deadbeef"
    assert rows[0].number is None  # omitted fields are overwritten, not preserved


@pytest.mark.db
def test_acts_vigenza_check_constraint(engine: Engine) -> None:
    with pytest.raises(Exception, match="ck_acts_vigenza"):
        upsert_act(
            engine,
            act_ref=_ACT_REF,
            act_type="legge",
            collection="Test",
            vigenza="bogus",
            file_path=_FILE_PATH,
        )


@pytest.mark.db
def test_upsert_progress_insert_then_update(engine: Engine) -> None:
    upsert_progress(engine, file_path=_FILE_PATH, commit_sha="aaa111")
    with engine.connect() as conn:
        row = conn.execute(
            select(ingestion_progress).where(
                ingestion_progress.c.file_path == _FILE_PATH
            )
        ).one()
    assert row.commit_sha == "aaa111"
    assert row.act_ref is None
    first_indexed_at = row.indexed_at
    assert first_indexed_at is not None

    upsert_progress(
        engine, file_path=_FILE_PATH, commit_sha="bbb222", act_ref=_ACT_REF
    )
    with engine.connect() as conn:
        rows = conn.execute(
            select(ingestion_progress).where(
                ingestion_progress.c.file_path == _FILE_PATH
            )
        ).all()
    assert len(rows) == 1
    assert rows[0].commit_sha == "bbb222"
    assert rows[0].act_ref == _ACT_REF
    assert rows[0].indexed_at >= first_indexed_at


@pytest.mark.db
def test_ingestion_runs_server_defaults(engine: Engine) -> None:
    with engine.begin() as conn:
        run_id = conn.execute(
            ingestion_runs.insert()
            .values(kind="bootstrap", status="running")
            .returning(ingestion_runs.c.id)
        ).scalar_one()
        row = conn.execute(
            select(ingestion_runs).where(ingestion_runs.c.id == run_id)
        ).one()
        assert row.files_processed == 0
        assert row.files_skipped == 0
        assert row.errors == []
        conn.execute(
            ingestion_runs.delete().where(ingestion_runs.c.id == run_id)
        )
