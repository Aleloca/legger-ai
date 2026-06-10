"""Alembic environment: URL from legger Settings, metadata from legger.db."""

import logging
from logging.config import fileConfig

from sqlalchemy import create_engine, pool

from alembic import context
from legger.db import metadata
from legger.settings import Settings

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

log = logging.getLogger("alembic.env")

# our SQLAlchemy Core metadata, for 'autogenerate' support
target_metadata = metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode: emit SQL without a DBAPI connection.

    Offline mode only needs a dialect, so a Settings failure falls back to
    the alembic.ini URL.
    """
    try:
        url = Settings().database_url
        log.info("offline mode: using DATABASE_URL from legger Settings")
    except Exception:
        url = config.get_main_option("sqlalchemy.url")
        log.warning(
            "offline mode: legger Settings unavailable, "
            "falling back to sqlalchemy.url from alembic.ini"
        )
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_server_default=True,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode: connect and apply.

    Online mode talks to the real database, so a misconfigured Settings must
    fail loudly here — silently falling back to the alembic.ini placeholder
    URL could migrate the wrong database.
    """
    try:
        url = Settings().database_url
    except Exception as exc:
        raise RuntimeError(
            "Cannot run online migrations: legger Settings failed to load "
            "DATABASE_URL (no alembic.ini fallback in online mode)"
        ) from exc
    log.info("online mode: using DATABASE_URL from legger Settings")

    # create_engine(url) directly: bypasses configparser, so no %-escaping
    # of the URL is needed (engine_from_config would interpolate '%').
    connectable = create_engine(url, poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_server_default=True,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
