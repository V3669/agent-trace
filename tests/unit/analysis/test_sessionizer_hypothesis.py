"""Property-based tests for the sessionizer module (PLAN.md §8.2).

Properties verified:
  P1 – _is_prefix: prefix + extension always satisfies _is_prefix.
  P2 – _is_prefix: empty list is a prefix of any list.
  P3 – _is_prefix: a longer list is never a prefix of a shorter list.
  P4 – Monotonic chain: a strictly-growing prefix chain produces a single session.
  P5 – Compaction: a shorter follow-up array never splits the session.
  P6 – Agent isolation: requests from two distinct agent_ids always land in
       disjoint session sets.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from agenttrace.analysis.sessionizer import _is_prefix, sessionize
from agenttrace.capture.db import apply_migrations, open_db

_MIGRATIONS_DIR = Path(__file__).parent.parent.parent.parent / "migrations"

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_ALNUM = "abcdefghijklmnopqrstuvwxyz0123456789"
_hash_st = st.text(min_size=2, max_size=16, alphabet=_ALNUM)
_hash_list_st = st.lists(_hash_st, min_size=0, max_size=8)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fresh_conn(tmpdir: str) -> tuple[Path, sqlite3.Connection]:
    db_path = Path(tmpdir) / "test.db"
    conn = open_db(db_path)
    apply_migrations(conn, _MIGRATIONS_DIR)
    return db_path, conn


def _insert(
    conn: sqlite3.Connection,
    recv_ts: str,
    hashes: list[str],
    agent_id: str = "claude_code",
    conn_id: str = "c1",
) -> None:
    conn.execute(
        "INSERT INTO requests "
        "(recv_ts, conn_id, agent_id, provider, message_hashes_json) "
        "VALUES (?, ?, ?, 'anthropic', ?)",
        (recv_ts, conn_id, agent_id, json.dumps(hashes)),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# P1–P3  Pure-function properties (no DB)
# ---------------------------------------------------------------------------


@given(prefix=_hash_list_st, extension=st.lists(_hash_st, min_size=0, max_size=5))
def test_prefix_extended_by_any_suffix_is_prefix(prefix: list[str], extension: list[str]) -> None:
    """prefix is always a prefix of prefix + extension."""
    assert _is_prefix(prefix, prefix + extension)


@given(candidate=_hash_list_st)
def test_empty_list_is_prefix_of_anything(candidate: list[str]) -> None:
    """The empty list is a prefix of every list."""
    assert _is_prefix([], candidate)


@given(
    base=st.lists(_hash_st, min_size=1, max_size=8),
    extra=st.lists(_hash_st, min_size=1, max_size=4),
)
def test_longer_list_not_prefix_of_base(base: list[str], extra: list[str]) -> None:
    """A strictly-longer list cannot be a prefix of the shorter base."""
    longer = base + extra
    assert not _is_prefix(longer, base)


# ---------------------------------------------------------------------------
# P4  Monotonic prefix chain → single session
# ---------------------------------------------------------------------------


@given(
    base=st.lists(_hash_st, min_size=1, max_size=5),
    extensions=st.lists(
        st.lists(_hash_st, min_size=1, max_size=4),
        min_size=1,
        max_size=5,
    ),
)
@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_monotonic_chain_produces_single_session(
    base: list[str], extensions: list[list[str]]
) -> None:
    """A strictly-growing prefix chain (within gap window) maps to one session."""
    chains: list[list[str]] = [base]
    for ext in extensions:
        chains.append(chains[-1] + ext)

    with tempfile.TemporaryDirectory() as tmpdir:
        _, conn = _fresh_conn(tmpdir)

        for i, hashes in enumerate(chains):
            # 2-minute steps; max i=6 → well within 30-min default gap
            ts = f"2026-01-01T00:{i:02d}:00Z"
            _insert(conn, ts, hashes)

        sessionize(conn)

        session_ids = conn.execute("SELECT DISTINCT session_id FROM session_events").fetchall()
        assert len(session_ids) == 1, (
            f"Monotonic chain produced {len(session_ids)} sessions, expected 1"
        )

        event_types = [
            r[0]
            for r in conn.execute("SELECT event_type FROM session_events ORDER BY id").fetchall()
        ]
        assert event_types[0] == "start"
        assert all(t in {"start", "continue"} for t in event_types)
        conn.close()


# ---------------------------------------------------------------------------
# P5  Compaction stays in the same session
# ---------------------------------------------------------------------------


@given(
    initial_hashes=st.lists(_hash_st, min_size=2, max_size=8),
    compact_hash=_hash_st,
)
@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_compaction_does_not_split_session(initial_hashes: list[str], compact_hash: str) -> None:
    """A shorter follow-up array (compaction) stays in the same session."""
    # initial_hashes has min_size=2, compact=[compact_hash] has length 1.
    # _is_prefix(initial_hashes, [compact_hash]) → False (len 2+ > len 1).
    # len([compact_hash]) <= len(initial_hashes) → compaction branch taken.
    with tempfile.TemporaryDirectory() as tmpdir:
        _, conn = _fresh_conn(tmpdir)
        _insert(conn, "2026-01-01T00:00:00Z", initial_hashes)
        _insert(conn, "2026-01-01T00:01:00Z", [compact_hash])
        sessionize(conn)

        session_ids = conn.execute("SELECT DISTINCT session_id FROM session_events").fetchall()
        assert len(session_ids) == 1, "Compaction must not create a new session"

        events = [
            r[0]
            for r in conn.execute("SELECT event_type FROM session_events ORDER BY id").fetchall()
        ]
        assert events[0] == "start"
        assert events[1] == "compaction"
        conn.close()


# ---------------------------------------------------------------------------
# P6  Agent isolation
# ---------------------------------------------------------------------------


@given(
    hashes_a=st.lists(_hash_st, min_size=1, max_size=5),
    hashes_b=st.lists(_hash_st, min_size=1, max_size=5),
)
@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_two_agents_have_disjoint_sessions(hashes_a: list[str], hashes_b: list[str]) -> None:
    """Requests from two distinct agent_ids always land in separate sessions."""
    with tempfile.TemporaryDirectory() as tmpdir:
        _, conn = _fresh_conn(tmpdir)
        _insert(conn, "2026-01-01T00:00:00Z", hashes_a, agent_id="agent_alpha")
        _insert(conn, "2026-01-01T00:01:00Z", hashes_b, agent_id="agent_beta")
        sessionize(conn)

        rows = conn.execute(
            "SELECT r.agent_id, se.session_id "
            "FROM session_events se "
            "JOIN requests r ON r.id = se.request_id"
        ).fetchall()

        agent_sessions: dict[str, set[str]] = {}
        for aid, sid in rows:
            agent_sessions.setdefault(str(aid), set()).add(str(sid))

        sessions_alpha = agent_sessions.get("agent_alpha", set())
        sessions_beta = agent_sessions.get("agent_beta", set())
        assert sessions_alpha.isdisjoint(sessions_beta), (
            f"Agents shared sessions: {sessions_alpha & sessions_beta}"
        )
        conn.close()
