"""Small, explicit SQLite schema migration runner for DSM.

The migration ledger is deliberately kept in the application instead of adding a
second migration framework dependency.  Migrations are append-only, run in one
transaction, and are recorded only after their schema change succeeds.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import ForeignKeyConstraint, UniqueConstraint, inspect
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from dsm.models import Base


class MigrationError(RuntimeError):
    """Raised when a database cannot be safely brought to the expected schema."""


MigrationApply = Callable[[Connection], None]


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    apply: MigrationApply


def _baseline_schema(connection: Connection) -> None:
    """Create DSM's pre-migration schema for new and pre-ledger databases."""
    Base.metadata.create_all(connection)


def _add_fan_config_polling_seconds(connection: Connection) -> None:
    """Add the polling interval introduced by the legacy inline migration."""
    inspector = inspect(connection)
    if "fan_configs" not in inspector.get_table_names():
        raise MigrationError("migration 2 requires the fan_configs table")

    columns = {column["name"] for column in inspector.get_columns("fan_configs")}
    if "polling_seconds" not in columns:
        connection.exec_driver_sql(
            "ALTER TABLE fan_configs "
            "ADD COLUMN polling_seconds INTEGER NOT NULL DEFAULT 20"
        )


MIGRATIONS: tuple[Migration, ...] = (
    Migration(1, "baseline_schema", _baseline_schema),
    Migration(2, "add_fan_config_polling_seconds", _add_fan_config_polling_seconds),
)
LATEST_VERSION = MIGRATIONS[-1].version


def _create_version_table(connection: Connection) -> None:
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def _validate_version_table(connection: Connection, *, require_primary_key: bool = True) -> None:
    """Check the pre-existing ledger before trusting its contents."""
    inspector = inspect(connection)
    columns = {column["name"]: column for column in inspector.get_columns("schema_migrations")}
    required_columns = {"version", "name", "applied_at"}
    missing_columns = sorted(required_columns - set(columns))
    if missing_columns:
        raise MigrationError(
            "database migration ledger is malformed; required columns are missing: "
            f"{', '.join(missing_columns)}"
        )

    primary_key = tuple(inspector.get_pk_constraint("schema_migrations").get("constrained_columns") or ())
    if require_primary_key and primary_key != ("version",):
        raise MigrationError(
            "database migration ledger is malformed; schema_migrations.version "
            "must be its primary key"
        )


def _applied_migrations(connection: Connection) -> dict[int, str]:
    rows = connection.exec_driver_sql("SELECT version, name FROM schema_migrations").all()
    malformed_rows = [
        row
        for row in rows
        if isinstance(row[0], bool) or not isinstance(row[0], int)
        or not isinstance(row[1], str) or not row[1]
    ]
    if malformed_rows:
        raise MigrationError("database migration ledger contains malformed version or name values")

    versions = [row[0] for row in rows]
    duplicates = sorted({version for version in versions if versions.count(version) > 1})
    if duplicates:
        raise MigrationError(
            "database migration ledger contains duplicate version rows: "
            f"{duplicates}"
        )
    return {row[0]: row[1] for row in rows}


def _validate_migration_history(applied: dict[int, str]) -> None:
    known_migrations = {migration.version: migration.name for migration in MIGRATIONS}
    unknown = set(applied) - set(known_migrations)
    if unknown:
        raise MigrationError(
            "database contains migrations newer than this DSM version: "
            f"{sorted(unknown)}"
        )

    expected_prefix = set(range(1, max(applied, default=0) + 1))
    if set(applied) != expected_prefix:
        raise MigrationError(
            "database migration history is not contiguous; refusing to guess how to repair it: "
            f"{sorted(applied)}"
        )

    renamed = [
        version for version, name in applied.items() if known_migrations[version] != name
    ]
    if renamed:
        raise MigrationError(
            "database migration names do not match this DSM version: "
            f"{sorted(renamed)}"
        )


def _validate_current_schema(connection: Connection) -> None:
    """Detect a recorded-but-incomplete schema instead of continuing unsafely."""
    inspector = inspect(connection)
    expected_tables = set(Base.metadata.tables)
    missing_tables = sorted(expected_tables - set(inspector.get_table_names()))
    if missing_tables:
        raise MigrationError(
            "database migration history is recorded but required tables are missing: "
            f"{', '.join(missing_tables)}"
        )

    missing_columns = {
        table_name: sorted(
            {column.name for column in table.columns}
            - {column["name"] for column in inspector.get_columns(table_name)}
        )
        for table_name, table in Base.metadata.tables.items()
    }
    incomplete_tables = {
        table_name: columns for table_name, columns in missing_columns.items() if columns
    }
    if incomplete_tables:
        details = "; ".join(
            f"{table_name} ({', '.join(columns)})"
            for table_name, columns in sorted(incomplete_tables.items())
        )
        raise MigrationError(
            "database migration history is recorded but required columns are missing: "
            f"{details}"
        )

    integrity_errors: list[str] = []
    for table_name, table in Base.metadata.tables.items():
        actual_primary_key = tuple(
            inspector.get_pk_constraint(table_name).get("constrained_columns") or ()
        )
        expected_primary_key = tuple(column.name for column in table.primary_key.columns)
        if actual_primary_key != expected_primary_key:
            integrity_errors.append(f"{table_name} primary key")

        actual_unique_sets = {
            tuple(constraint.get("column_names") or ())
            for constraint in inspector.get_unique_constraints(table_name)
        }
        actual_indexes = inspector.get_indexes(table_name)
        actual_unique_sets.update(
            tuple(index.get("column_names") or ())
            for index in actual_indexes
            if index.get("unique")
        )
        if connection.dialect.name == "sqlite":
            quote_identifier = connection.dialect.identifier_preparer.quote
            sqlite_indexes = connection.exec_driver_sql(
                f"PRAGMA index_list({quote_identifier(table_name)})"
            ).mappings()
            for sqlite_index in sqlite_indexes:
                if not sqlite_index["unique"]:
                    continue
                index_name = quote_identifier(sqlite_index["name"])
                column_rows = connection.exec_driver_sql(
                    f"PRAGMA index_info({index_name})"
                ).mappings()
                actual_unique_sets.add(tuple(row["name"] for row in column_rows))
        expected_unique_sets = {
            tuple(column.name for column in constraint.columns)
            for constraint in table.constraints
            if isinstance(constraint, UniqueConstraint)
        }
        expected_unique_sets.update((column.name,) for column in table.columns if column.unique)
        if not expected_unique_sets.issubset(actual_unique_sets):
            integrity_errors.append(f"{table_name} unique constraints")

        actual_nonunique_index_sets = {
            tuple(index.get("column_names") or ())
            for index in actual_indexes
            if not index.get("unique")
        }
        expected_unique_index_sets = {
            tuple(column.name for column in index.columns)
            for index in table.indexes
            if index.unique
        }
        expected_nonunique_index_sets = {
            tuple(column.name for column in index.columns)
            for index in table.indexes
            if not index.unique
        }
        # SQLite may implement a UNIQUE column/index with an implicit
        # sqlite_autoindex that its SQLAlchemy inspector does not expose.
        if (
            not expected_unique_index_sets.issubset(actual_unique_sets)
            or not expected_nonunique_index_sets.issubset(actual_nonunique_index_sets)
        ):
            integrity_errors.append(f"{table_name} indexes")

        actual_foreign_keys = {
            (
                tuple(foreign_key.get("constrained_columns") or ()),
                foreign_key.get("referred_table"),
                tuple(foreign_key.get("referred_columns") or ()),
                str(foreign_key.get("options", {}).get("ondelete") or "").upper(),
            )
            for foreign_key in inspector.get_foreign_keys(table_name)
        }
        expected_foreign_keys = {
            (
                tuple(element.parent.name for element in constraint.elements),
                constraint.elements[0].column.table.name,
                tuple(element.column.name for element in constraint.elements),
                str(constraint.elements[0].ondelete or "").upper(),
            )
            for constraint in table.constraints
            if isinstance(constraint, ForeignKeyConstraint)
        }
        if not expected_foreign_keys.issubset(actual_foreign_keys):
            integrity_errors.append(f"{table_name} foreign keys")

    if integrity_errors:
        raise MigrationError(
            "database migration history is recorded but required schema integrity is missing: "
            f"{'; '.join(integrity_errors)}"
        )

    fan_columns = {column["name"] for column in inspector.get_columns("fan_configs")}
    if "polling_seconds" not in fan_columns:
        raise MigrationError(
            "database migration 2 is recorded but fan_configs.polling_seconds is missing"
        )


def migrate(connection: Connection) -> None:
    """Apply all outstanding migrations using the caller's transaction."""
    _create_version_table(connection)
    # Check column shape before querying the ledger.  Duplicate rows are
    # checked next so an old table without a primary key is not silently
    # collapsed into a dictionary before we reject its missing key.
    _validate_version_table(connection, require_primary_key=False)
    applied = _applied_migrations(connection)
    _validate_version_table(connection)
    _validate_migration_history(applied)

    for migration in MIGRATIONS:
        if migration.version in applied:
            continue
        try:
            migration.apply(connection)
            connection.exec_driver_sql(
                "INSERT INTO schema_migrations (version, name) VALUES (?, ?)",
                (migration.version, migration.name),
            )
        except Exception as exc:
            raise MigrationError(
                f"migration {migration.version} ({migration.name}) failed; database was not started"
            ) from exc

    _validate_current_schema(connection)


async def run_migrations(engine: AsyncEngine) -> None:
    """Apply outstanding migrations atomically through an async SQLAlchemy engine."""
    try:
        async with engine.connect() as connection:
            # SQLite's deferred transactions let two startup processes inspect
            # the same old schema before either writes.  Take the write lock
            # first so a second process waits and then observes the first
            # process's completed migration ledger.
            if connection.dialect.name == "sqlite":
                # SQLite otherwise fails immediately when another DSM process is
                # finishing startup and holds the migration write lock.
                await connection.exec_driver_sql("PRAGMA busy_timeout = 5000")
            await connection.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                await connection.run_sync(migrate)
            except Exception:
                await connection.rollback()
                raise
            else:
                await connection.commit()
    except MigrationError:
        raise
    except SQLAlchemyError as exc:
        raise MigrationError("database migration setup failed; database was not started") from exc


async def _main() -> None:
    """Run migrations as ``python -m dsm.migrations``."""
    from dsm.database import engine

    try:
        await run_migrations(engine)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
