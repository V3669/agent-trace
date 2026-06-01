"""Shared DuckDB connection utilities.

All analytics modules open a fresh in-memory DuckDB connection, attach the
SQLite store read-only, and close the connection when done.  This module
centralises that lifecycle so every caller gets the same setup with one
INSTALL sqlite / LOAD sqlite sequence.

Usage::

    with open_duckdb(db_path) as duck:
        rows = duck.execute("SELECT ...").fetchall()

The context manager always closes the connection, even on error.
"""

from __future__ import annotations

import contextlib
from collections.abc import Generator
from pathlib import Path

import duckdb
import structlog

logger = structlog.get_logger(__name__)

# INSTALL is idempotent (no-op if already installed); LOAD is per-connection.
_SQLITE_EXT_SQL = "INSTALL sqlite; LOAD sqlite;"


@contextlib.contextmanager
def open_duckdb(db_path: Path) -> Generator[duckdb.DuckDBPyConnection, None, None]:
    """Yield an in-memory DuckDB connection with the SQLite store attached read-only.

    The connection is always closed in the finally block.

    Args:
        db_path: Absolute path to the SQLite store file.

    Yields:
        An open DuckDB connection with ``s.*`` pointing at the SQLite tables.
    """
    duck: duckdb.DuckDBPyConnection | None = None
    try:
        duck = duckdb.connect(":memory:")
        duck.execute(_SQLITE_EXT_SQL)
        # Use positional string interpolation for the path — DuckDB's ATTACH
        # does not support prepared-statement parameters for the path literal.
        safe_path = str(db_path).replace("'", "''")
        duck.execute(f"ATTACH '{safe_path}' AS s (TYPE sqlite, READ_ONLY)")
        yield duck
    finally:
        if duck is not None:
            try:
                duck.close()
            except Exception:
                logger.exception("duckdb.close_error")
