"""Behavior contracts for DSM's SQLite migration runner."""

from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy import Column, Integer, String, Table, inspect
from sqlalchemy.ext.asyncio import create_async_engine

from dsm import database
import dsm.migrations as migrations
from dsm.migrations import LATEST_VERSION, Migration, MigrationError, run_migrations
from dsm.models import Base


async def _temporary_database(monkeypatch: pytest.MonkeyPatch, db_path) -> None:
    """Point the real database module at a per-test SQLite database."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database.settings, "db_path", str(db_path))


@pytest.mark.asyncio
async def test_fresh_database_initializes_to_latest_schema(tmp_path, monkeypatch):
    db_path = tmp_path / "fresh.db"
    await _temporary_database(monkeypatch, db_path)

    await database.init_db()

    async with database.engine.connect() as connection:
        tables = await connection.run_sync(lambda sync: set(inspect(sync).get_table_names()))
        versions = (await connection.exec_driver_sql(
            "SELECT version FROM schema_migrations ORDER BY version"
        )).scalars().all()

    assert set(Base.metadata.tables).issubset(tables)
    assert versions == list(range(1, LATEST_VERSION + 1))
    await database.engine.dispose()


@pytest.mark.asyncio
async def test_legacy_database_gains_polling_seconds_without_losing_data(tmp_path):
    db_path = tmp_path / "legacy.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")

    # Build the current legacy shape: all prior tables exist, but fan_configs
    # predates polling_seconds and already contains an operator's row.
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.exec_driver_sql("DROP TABLE fan_configs")
        await connection.exec_driver_sql(
            """
            CREATE TABLE fan_configs (
                id INTEGER NOT NULL PRIMARY KEY,
                server_id INTEGER NOT NULL,
                mode VARCHAR(16), cpu_temp_min FLOAT, cpu_temp_max FLOAT,
                disk_temp_min FLOAT, disk_temp_max FLOAT, manual_speed INTEGER,
                auto_control BOOLEAN, updated_at DATETIME,
                FOREIGN KEY(server_id) REFERENCES servers (id) ON DELETE CASCADE
            )
            """
        )
        await connection.exec_driver_sql(
            "INSERT INTO servers (id, name, ipmi_ip, ipmi_user, ipmi_password_enc) "
            "VALUES (1, 'legacy-server', '192.0.2.10', 'operator', 'encrypted')"
        )
        await connection.exec_driver_sql(
            "INSERT INTO fan_configs (id, server_id, mode, manual_speed) "
            "VALUES (7, 1, 'manual', 42)"
        )

    await run_migrations(engine)

    async with engine.connect() as connection:
        row = (await connection.exec_driver_sql(
            "SELECT id, server_id, mode, manual_speed, polling_seconds FROM fan_configs"
        )).one()
        versions = (await connection.exec_driver_sql(
            "SELECT version FROM schema_migrations ORDER BY version"
        )).scalars().all()

    assert row == (7, 1, "manual", 42, 20)
    assert versions == list(range(1, LATEST_VERSION + 1))
    await engine.dispose()


@pytest.mark.asyncio
async def test_init_db_is_repeatable(tmp_path, monkeypatch):
    db_path = tmp_path / "repeatable.db"
    await _temporary_database(monkeypatch, db_path)

    await database.init_db()
    await database.init_db()

    with sqlite3.connect(db_path) as connection:
        versions = connection.execute(
            "SELECT version, COUNT(*) FROM schema_migrations GROUP BY version ORDER BY version"
        ).fetchall()
        polling_column = connection.execute("PRAGMA table_info(fan_configs)").fetchall()

    assert versions == [(version, 1) for version in range(1, LATEST_VERSION + 1)]
    assert "polling_seconds" in {column[1] for column in polling_column}
    await database.engine.dispose()


@pytest.mark.asyncio
async def test_failed_migration_rolls_back_schema_and_ledger(tmp_path, monkeypatch):
    db_path = tmp_path / "rollback.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")

    def fail_after_schema_change(connection):
        connection.exec_driver_sql("CREATE TABLE should_not_survive (id INTEGER PRIMARY KEY)")
        raise RuntimeError("intentional migration failure")

    monkeypatch.setattr(
        migrations,
        "MIGRATIONS",
        migrations.MIGRATIONS + (Migration(LATEST_VERSION + 1, "intentional_failure", fail_after_schema_change),),
    )

    with pytest.raises(MigrationError, match="intentional_failure"):
        await run_migrations(engine)

    with sqlite3.connect(db_path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "should_not_survive" not in tables
    assert "schema_migrations" not in tables
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("rows", "error"),
    [
        ([(1, "baseline_schema"), (99, "future")], "newer"),
        ([(2, "add_fan_config_polling_seconds")], "not contiguous"),
        ([(1, "renamed_baseline")], "names do not match"),
    ],
)
async def test_unknown_gapped_and_renamed_histories_are_rejected(tmp_path, rows, error):
    db_path = tmp_path / "bad-history.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    await run_migrations(engine)
    async with engine.begin() as connection:
        await connection.exec_driver_sql("DELETE FROM schema_migrations")
        for version, name in rows:
            await connection.exec_driver_sql(
                "INSERT INTO schema_migrations (version, name) VALUES (?, ?)", (version, name)
            )

    with pytest.raises(MigrationError, match=error):
        await run_migrations(engine)
    await engine.dispose()


@pytest.mark.asyncio
async def test_duplicate_ledger_rows_are_rejected_before_collapsing_history(tmp_path):
    db_path = tmp_path / "duplicate-history.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as connection:
        await connection.exec_driver_sql(
            "CREATE TABLE schema_migrations (version INTEGER NOT NULL, name TEXT NOT NULL, applied_at TEXT)"
        )
        await connection.exec_driver_sql(
            "INSERT INTO schema_migrations (version, name) VALUES (1, 'baseline_schema'), (1, 'baseline_schema')"
        )

    with pytest.raises(MigrationError, match="duplicate version rows"):
        await run_migrations(engine)
    await engine.dispose()


@pytest.mark.asyncio
async def test_recorded_history_accepts_unique_index_backed_by_sqlite_unique_constraint(tmp_path):
    db_path = tmp_path / "missing-index.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    await run_migrations(engine)
    table = Table(
        "unique_index_backed_by_constraint",
        Base.metadata,
        Column("id", Integer, primary_key=True),
        Column("name", String(64), unique=True, nullable=False, index=True),
    )
    try:
        async with engine.begin() as connection:
            # This UNIQUE constraint is enforced by SQLite's implicit
            # auto-index, which SQLAlchemy does not expose as get_indexes().
            await connection.exec_driver_sql(
                "CREATE TABLE unique_index_backed_by_constraint ("
                "id INTEGER NOT NULL PRIMARY KEY, "
                "name VARCHAR(64) NOT NULL UNIQUE)"
            )

        await run_migrations(engine)
    finally:
        Base.metadata.remove(table)
        await engine.dispose()


@pytest.mark.asyncio
async def test_recorded_history_rejects_missing_nonunique_index(tmp_path):
    db_path = tmp_path / "missing-index.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    await run_migrations(engine)
    async with engine.begin() as connection:
        await connection.exec_driver_sql("DROP INDEX ix_servers_ipmi_ip")

    with pytest.raises(MigrationError, match="servers indexes"):
        await run_migrations(engine)
    await engine.dispose()
