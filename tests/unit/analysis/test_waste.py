import json
import sqlite3
from pathlib import Path

import pytest

from agenttrace.analysis.sessionizer import sessionize
from agenttrace.analysis.waste import compute_waste, get_latest_session_id
from agenttrace.capture.db import apply_migrations, open_db

_MIGRATIONS_DIR = Path(__file__).parent.parent.parent.parent / "migrations"


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "test.db"
    conn = open_db(p)
    apply_migrations(conn, _MIGRATIONS_DIR)
    conn.close()
    return p


def _insert_request_with_usage(
    conn: sqlite3.Connection,
    recv_ts: str,
    model: str,
    input_tokens: int,
    cache_read: int = 0,
    cache_create: int = 0,
    hashes: list[str] | None = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO requests (recv_ts, conn_id, agent_id, provider, model,
            usage_input, usage_output, cache_read_input, cache_creation_input,
            usage_source, message_hashes_json)
        VALUES (?, 'c1', 'claude_code', 'anthropic', ?, ?, 0, ?, ?, 'reported', ?)
        """,
        (recv_ts, model, input_tokens, cache_read, cache_create, json.dumps(hashes or [])),
    )
    conn.commit()
    return cur.lastrowid  # type: ignore[return-value]


def _insert_fre(
    conn: sqlite3.Connection, request_id: int, path: str, content_hash: str, tokens: int
) -> None:
    conn.execute(
        "INSERT INTO file_read_events (request_id, path, content_hash, approx_tokens) "
        "VALUES (?, ?, ?, ?)",
        (request_id, path, content_hash, tokens),
    )
    conn.commit()


_MODEL = "claude-sonnet-4-6"
_T0 = "2026-01-01T00:00:00Z"
_T1 = "2026-01-01T00:01:00Z"


def test_zero_waste_no_rereads(db_path: Path) -> None:
    conn = open_db(db_path)
    r1 = _insert_request_with_usage(conn, _T0, _MODEL, 100, hashes=["h1"])
    r2 = _insert_request_with_usage(conn, _T1, _MODEL, 150, hashes=["h1", "h2"])
    _insert_fre(conn, r1, "/foo.py", "hash_a", 50)
    _insert_fre(conn, r2, "/bar.py", "hash_b", 60)
    sessionize(conn)
    conn.close()

    session_id = get_latest_session_id(db_path)
    assert session_id is not None
    report = compute_waste(db_path, session_id)
    assert report.wasted_cost == 0.0
    assert report.avoidable_pct == 0.0


def test_waste_detected_on_reread(db_path: Path) -> None:
    conn = open_db(db_path)
    r1 = _insert_request_with_usage(conn, _T0, _MODEL, 100, hashes=["h1"])
    r2 = _insert_request_with_usage(conn, _T1, _MODEL, 150, hashes=["h1", "h2"])
    _insert_fre(conn, r1, "/foo.py", "same_hash", 50)
    _insert_fre(conn, r2, "/foo.py", "same_hash", 50)
    sessionize(conn)
    conn.close()

    session_id = get_latest_session_id(db_path)
    assert session_id is not None
    report = compute_waste(db_path, session_id)
    assert report.wasted_cost > 0.0
    assert report.avoidable_pct > 0.0


def test_changed_file_not_counted_as_waste(db_path: Path) -> None:
    conn = open_db(db_path)
    r1 = _insert_request_with_usage(conn, _T0, _MODEL, 100, hashes=["h1"])
    r2 = _insert_request_with_usage(conn, _T1, _MODEL, 150, hashes=["h1", "h2"])
    _insert_fre(conn, r1, "/foo.py", "hash_v1", 50)
    _insert_fre(conn, r2, "/foo.py", "hash_v2", 55)  # same path, different content
    sessionize(conn)
    conn.close()

    session_id = get_latest_session_id(db_path)
    assert session_id is not None
    report = compute_waste(db_path, session_id)
    assert report.wasted_cost == 0.0


def test_get_latest_session_id_empty(db_path: Path) -> None:
    result = get_latest_session_id(db_path)
    assert result is None
