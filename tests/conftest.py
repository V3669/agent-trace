"""Shared pytest fixtures for AgentTrace tests."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# SQLite test database fixture
# ---------------------------------------------------------------------------


def _apply_schema(conn: sqlite3.Connection) -> None:
    """Apply the production schema (migrations/0001_init.sql) to a test DB."""
    migrations_dir = Path(__file__).parent.parent / "migrations"
    sql_file = migrations_dir / "0001_init.sql"
    conn.executescript(sql_file.read_text())


def _insert_request(
    conn: sqlite3.Connection,
    *,
    recv_ts: str = "2026-01-01T00:00:00Z",
    conn_id: str = "conn-1",
    agent_id: str = "claude_code",
    provider: str = "anthropic",
    model: str = "claude-opus-4-8",
    usage_input: int = 1000,
    usage_output: int = 200,
    cache_read_input: int = 0,
    cache_creation_input: int = 0,
    usage_source: str = "reported",
    body_blob: bytes | None = None,
    message_hashes_json: str | None = None,
    system_prompt_hash: str | None = None,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO requests (
            recv_ts, conn_id, agent_id, provider, model,
            usage_input, usage_output, cache_read_input, cache_creation_input,
            usage_source, body_blob, message_hashes_json, system_prompt_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            recv_ts,
            conn_id,
            agent_id,
            provider,
            model,
            usage_input,
            usage_output,
            cache_read_input,
            cache_creation_input,
            usage_source,
            body_blob,
            message_hashes_json or json.dumps([]),
            system_prompt_hash,
        ),
    )
    conn.commit()
    return int(cursor.lastrowid)  # type: ignore[arg-type]


def _insert_session_event(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    request_id: int,
    event_type: str = "start",
    parent_session_id: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO session_events (session_id, request_id, event_type, parent_session_id) "
        "VALUES (?, ?, ?, ?)",
        (session_id, request_id, event_type, parent_session_id),
    )
    conn.commit()


def _insert_file_read_event(
    conn: sqlite3.Connection,
    *,
    request_id: int,
    path: str,
    content_hash: str,
    approx_tokens: int = 100,
) -> None:
    conn.execute(
        "INSERT INTO file_read_events (request_id, path, content_hash, approx_tokens) "
        "VALUES (?, ?, ?, ?)",
        (request_id, path, content_hash, approx_tokens),
    )
    conn.commit()


@pytest.fixture()
def tmp_db(tmp_path: Path) -> tuple[Path, sqlite3.Connection]:
    """Return (db_path, connection) for a fresh test SQLite database."""
    db_path = tmp_path / "test_store.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _apply_schema(conn)
    return db_path, conn


@pytest.fixture()
def session_with_rerereads(
    tmp_db: tuple[Path, sqlite3.Connection],
) -> tuple[Path, str]:
    """DB with one session containing two re-reads of the same file.

    Session 'sess-1':
      req 1 — reads foo.py (hash 'aaa', 100 tok)
      req 2 — reads foo.py again (same hash, 100 tok)  ← re-read
      req 3 — normal request, no file reads
    """
    db_path, conn = tmp_db
    session_id = "sess-1"

    r1 = _insert_request(conn, recv_ts="2026-01-01T00:00:01Z", conn_id="c1", usage_input=500)
    r2 = _insert_request(conn, recv_ts="2026-01-01T00:00:02Z", conn_id="c1", usage_input=600)
    r3 = _insert_request(conn, recv_ts="2026-01-01T00:00:03Z", conn_id="c1", usage_input=300)

    _insert_session_event(conn, session_id=session_id, request_id=r1, event_type="start")
    _insert_session_event(conn, session_id=session_id, request_id=r2, event_type="continue")
    _insert_session_event(conn, session_id=session_id, request_id=r3, event_type="continue")

    for req_id in (r1, r2):
        _insert_file_read_event(
            conn, request_id=req_id, path="foo.py", content_hash="aaa", approx_tokens=100
        )

    return db_path, session_id
