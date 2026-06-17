"""Alembic env (async + autogenerate-aware, schema=public)."""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from ragbot.config.settings import get_settings
from ragbot.infrastructure.db.models import RAGBOT_SCHEMA, Base  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

settings = get_settings()
# Priority: ALEMBIC_SQLALCHEMY_URL (sync) > DATABASE_URL (may be async) > settings.
_url = (
    os.getenv("ALEMBIC_SQLALCHEMY_URL")
    or os.getenv("DATABASE_URL")
    or str(settings.database.url)
)
# Alembic needs a sync driver. Strip the +asyncpg suffix if present.
_url = _url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
config.set_main_option("sqlalchemy.url", _url)


def _configure(connection: Connection | None = None, url: str | None = None) -> None:
    context.configure(
        connection=connection,
        url=url,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        include_schemas=True,
        version_table_schema="public",
        literal_binds=url is not None,
        dialect_opts={"paramstyle": "named"} if url is not None else {},
    )


def run_migrations_offline() -> None:
    _configure(url=config.get_main_option("sqlalchemy.url"))
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    _configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


def _is_async_url(url: str) -> bool:
    return "+asyncpg" in url or "+aiosqlite" in url


def run_migrations_online_sync() -> None:
    from sqlalchemy import create_engine

    engine = create_engine(
        config.get_main_option("sqlalchemy.url"),
        poolclass=pool.NullPool,
    )
    # engine.begin() opens a transaction and commits on successful exit.
    with engine.begin() as connection:
        do_run_migrations(connection)
    engine.dispose()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    if _is_async_url(config.get_main_option("sqlalchemy.url")):
        asyncio.run(run_async_migrations())
    else:
        run_migrations_online_sync()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
