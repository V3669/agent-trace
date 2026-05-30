from __future__ import annotations

import sqlite3
from pathlib import Path

_OPEN_PRAGMAS = """\
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=5000;
"""

SCHEMA_VERSION = "0001"


def open_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.executescript(_OPEN_PRAGMAS)
    return conn


def apply_migrations(conn: sqlite3.Connection, migrations_dir: Path) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_meta (
            schema_version TEXT NOT NULL,
            otel_genai_convention TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.commit()

    row = conn.execute(
        "SELECT schema_version FROM schema_meta ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    applied = row[0] if row else None

    migration_files = sorted(migrations_dir.glob("*.sql"))
    for mf in migration_files:
        version = mf.stem.split("_")[0]
        if applied is not None and version <= applied:
            continue
        sql = mf.read_text(encoding="utf-8")
        with conn:
            conn.executescript(sql)
            conn.execute(
                "INSERT INTO schema_meta (schema_version, created_at) VALUES (?, datetime('now'))",
                (version,),
            )
