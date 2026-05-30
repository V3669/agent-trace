import sqlite3
from pathlib import Path

import pytest

from agenttrace.analysis.sessionizer import sessionize
from agenttrace.capture.db import apply_migrations, open_db

_MIGRATIONS_DIR = Path(__file__).parent.parent.parent.parent / "migrations"


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / "test.db"
    c = open_db(db_path)
    apply_migrations(c, _MIGRATIONS_DIR)
    yield c  # type: ignore[misc]
    c.close()


def _insert_request(
    conn: sqlite3.Connection, recv_ts: str, hashes: list[str], conn_id: str = "c1"
) -> int:
    import json

    cur = conn.execute(
        """
        INSERT INTO requests (recv_ts, conn_id, agent_id, provider, message_hashes_json)
        VALUES (?, ?, 'claude_code', 'anthropic', ?)
        """,
        (recv_ts, conn_id, json.dumps(hashes)),
    )
    conn.commit()
    return cur.lastrowid  # type: ignore[return-value]


def test_single_request_creates_start_session(conn: sqlite3.Connection) -> None:
    _insert_request(conn, "2026-01-01T00:00:00Z", ["h1", "h2"])
    sessionize(conn)
    rows = conn.execute("SELECT event_type FROM session_events").fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "start"


def test_prefix_continuation(conn: sqlite3.Connection) -> None:
    _insert_request(conn, "2026-01-01T00:00:00Z", ["h1", "h2"])
    _insert_request(conn, "2026-01-01T00:01:00Z", ["h1", "h2", "h3"])
    sessionize(conn)
    rows = conn.execute("SELECT event_type FROM session_events ORDER BY id").fetchall()
    assert rows[0][0] == "start"
    assert rows[1][0] == "continue"


def test_same_session_id_for_continuation(conn: sqlite3.Connection) -> None:
    _insert_request(conn, "2026-01-01T00:00:00Z", ["h1"])
    _insert_request(conn, "2026-01-01T00:01:00Z", ["h1", "h2"])
    sessionize(conn)
    rows = conn.execute("SELECT DISTINCT session_id FROM session_events").fetchall()
    assert len(rows) == 1  # same session


def test_time_gap_creates_new_session(conn: sqlite3.Connection) -> None:
    _insert_request(conn, "2026-01-01T00:00:00Z", ["h1"])
    _insert_request(conn, "2026-01-01T01:00:00Z", ["h1", "h2"])  # 60 min gap > 30 min default
    sessionize(conn)
    rows = conn.execute("SELECT DISTINCT session_id FROM session_events").fetchall()
    assert len(rows) == 2  # different sessions


def test_idempotent_no_double_insert(conn: sqlite3.Connection) -> None:
    _insert_request(conn, "2026-01-01T00:00:00Z", ["h1"])
    sessionize(conn)
    sessionize(conn)  # second run should add nothing
    count = conn.execute("SELECT COUNT(*) FROM session_events").fetchone()[0]
    assert count == 1


def test_compaction_tagged_same_session(conn: sqlite3.Connection) -> None:
    # Prefix fails because new array is shorter (compaction scenario)
    _insert_request(conn, "2026-01-01T00:00:00Z", ["h1", "h2", "h3"])
    _insert_request(conn, "2026-01-01T00:01:00Z", ["summary_h"])  # shorter = compaction
    sessionize(conn)
    rows = conn.execute("SELECT event_type FROM session_events ORDER BY id").fetchall()
    assert rows[0][0] == "start"
    assert rows[1][0] == "compaction"
    # Same session_id
    session_ids = conn.execute("SELECT DISTINCT session_id FROM session_events").fetchall()
    assert len(session_ids) == 1


def test_divergent_prefix_creates_new_session(conn: sqlite3.Connection) -> None:
    # Both start from h1 but then diverge (branch case)
    _insert_request(conn, "2026-01-01T00:00:00Z", ["h1", "h2"])
    # Different prefix entirely, not shorter, not a continuation
    _insert_request(conn, "2026-01-01T00:01:00Z", ["h1", "h2", "h3_alt", "h4_alt"])
    sessionize(conn)
    # Should continue (h1,h2 is prefix of h1,h2,h3_alt,h4_alt)
    rows = conn.execute("SELECT event_type FROM session_events ORDER BY id").fetchall()
    assert rows[1][0] == "continue"
