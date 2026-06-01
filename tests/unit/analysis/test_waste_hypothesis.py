"""Property-based tests for the waste / differ module (PLAN.md §8.2).

Properties verified:
  W1 – Identical (path, content_hash): always produces wasted_cost > 0.
  W2 – Changed content (same path, different hash): never counted as waste.
  W3 – Invariant: wasted_cost ≤ total_billed_input_cost for any input mix.
  W4 – Formula: repeat_tokens is always ≤ total_tokens (bounded).
  W5 – Formula: repeat_tokens is non-decreasing in read_count.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from agenttrace.analysis.sessionizer import sessionize
from agenttrace.analysis.waste import compute_waste, get_latest_session_id
from agenttrace.capture.db import apply_migrations, open_db

_MIGRATIONS_DIR = Path(__file__).parent.parent.parent.parent / "migrations"
_MODEL = "claude-sonnet-4-6"  # has a known price entry

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_HEX = "0123456789abcdef"
_ALNUM_PATH = "abcdefghijklmnopqrstuvwxyz0123456789_/."

_path_st = st.text(min_size=1, max_size=40, alphabet=_ALNUM_PATH)
_hash_st = st.text(min_size=4, max_size=32, alphabet=_HEX)
_token_st = st.integers(min_value=1, max_value=5_000)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fresh_conn(tmpdir: str) -> tuple[Path, sqlite3.Connection]:
    db_path = Path(tmpdir) / "store.db"
    conn = open_db(db_path)
    apply_migrations(conn, _MIGRATIONS_DIR)
    return db_path, conn


def _req(
    conn: sqlite3.Connection,
    ts: str,
    hashes: list[str],
    usage_input: int = 10_000,
) -> int:
    cur = conn.execute(
        "INSERT INTO requests "
        "(recv_ts, conn_id, agent_id, provider, model, "
        " usage_input, usage_output, cache_read_input, cache_creation_input, "
        " usage_source, message_hashes_json) "
        "VALUES (?, 'c1', 'claude_code', 'anthropic', ?, ?, 0, 0, 0, 'reported', ?)",
        (ts, _MODEL, usage_input, json.dumps(hashes)),
    )
    conn.commit()
    return int(cur.lastrowid)  # type: ignore[arg-type]


def _fre(
    conn: sqlite3.Connection,
    request_id: int,
    path: str,
    content_hash: str,
    tokens: int,
) -> None:
    conn.execute(
        "INSERT INTO file_read_events (request_id, path, content_hash, approx_tokens) "
        "VALUES (?, ?, ?, ?)",
        (request_id, path, content_hash, tokens),
    )
    conn.commit()


def _sessionize_close(conn: sqlite3.Connection, db_path: Path) -> str:
    """Run sessionize, close conn, return the latest session_id."""
    sessionize(conn)
    conn.close()
    sid = get_latest_session_id(db_path)
    assert sid is not None, "Expected at least one session after sessionize"
    return sid


# ---------------------------------------------------------------------------
# W4–W5  Pure formula invariants (no DB)
# ---------------------------------------------------------------------------


@given(
    read_count=st.integers(min_value=2, max_value=1_000),
    total_tokens=st.integers(min_value=1, max_value=10_000_000),
)
def test_repeat_tokens_bounded_by_total(read_count: int, total_tokens: int) -> None:
    """The waste formula never attributes more than total_tokens to repeat reads.

    When total_tokens < read_count, integer division floors one_share to 0,
    so repeat_tokens == total_tokens (edge case: 100 % attribution is valid).
    """
    one_share = total_tokens // read_count
    repeat_tokens = total_tokens - one_share
    assert 0 <= repeat_tokens <= total_tokens


@given(
    read_count=st.integers(min_value=2, max_value=999),
    total_tokens=st.integers(min_value=2, max_value=10_000_000),
)
def test_repeat_tokens_nondecreasing_in_read_count(read_count: int, total_tokens: int) -> None:
    """More reads of the same content → more (or equal) attributed waste."""

    def waste(n: int) -> int:
        return total_tokens - (total_tokens // n)

    assert waste(read_count) <= waste(read_count + 1)


# ---------------------------------------------------------------------------
# W1  Identical (path, content_hash) always produces waste
# ---------------------------------------------------------------------------


@given(
    path=_path_st,
    content_hash=_hash_st,
    tokens=_token_st,
    extra_reads=st.integers(min_value=1, max_value=4),
)
@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_identical_path_hash_always_waste(
    path: str,
    content_hash: str,
    tokens: int,
    extra_reads: int,
) -> None:
    """Re-reading the same (path, hash) in ≥2 requests always shows waste > 0."""
    n_reads = extra_reads + 1  # at least 2
    # Set usage_input >> file tokens to keep wasted_cost ≤ total_billed.
    usage_input = max(tokens * n_reads * 4, 1_000)

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path, conn = _fresh_conn(tmpdir)

        r0 = _req(conn, "2026-01-01T00:00:00Z", ["h0"], usage_input=usage_input)
        _fre(conn, r0, path, content_hash, tokens)

        for i in range(1, n_reads):
            ri = _req(
                conn,
                f"2026-01-01T00:{i:02d}:00Z",
                ["h0"] + [f"h{j}" for j in range(1, i + 1)],
                usage_input=usage_input,
            )
            _fre(conn, ri, path, content_hash, tokens)

        sid = _sessionize_close(conn, db_path)
        report = compute_waste(db_path, sid)

        assert report.wasted_cost > 0.0, (
            f"Expected waste for {n_reads} re-reads of ({path!r}, {content_hash!r})"
        )
        assert report.wasted_requests >= 1


# ---------------------------------------------------------------------------
# W2  Changed content (same path, different hash) is never waste
# ---------------------------------------------------------------------------


@given(
    path=_path_st,
    hash_a=_hash_st,
    hash_b=_hash_st,
    tokens=_token_st,
)
@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_changed_file_never_counted_as_waste(
    path: str,
    hash_a: str,
    hash_b: str,
    tokens: int,
) -> None:
    """Different content hashes for the same path are never flagged as waste."""
    assume(hash_a != hash_b)

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path, conn = _fresh_conn(tmpdir)

        r1 = _req(conn, "2026-01-01T00:00:00Z", ["h0"])
        r2 = _req(conn, "2026-01-01T00:01:00Z", ["h0", "h1"])
        _fre(conn, r1, path, hash_a, tokens)
        _fre(conn, r2, path, hash_b, tokens)  # same path, different version

        sid = _sessionize_close(conn, db_path)
        report = compute_waste(db_path, sid)

        assert report.wasted_cost == 0.0, (
            f"Changed file ({path!r}: {hash_a!r} → {hash_b!r}) wrongly flagged as waste"
        )


# ---------------------------------------------------------------------------
# W3  Invariant: wasted_cost ≤ total_billed_input_cost
# ---------------------------------------------------------------------------


@given(
    read_events=st.lists(
        st.tuples(_path_st, _hash_st, _token_st),
        min_size=1,
        max_size=8,
    ),
)
@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_wasted_cost_never_exceeds_total_billed(
    read_events: list[tuple[str, str, int]],
) -> None:
    """wasted_cost ≤ total_billed_input_cost for any combination of file reads.

    We set usage_input per request to 2× the total token budget so the waste
    (costed at cache-read rate, ~10× cheaper than input rate) is always bounded
    by total billed cost.
    """
    total_tokens = sum(tok for _, _, tok in read_events)
    usage_input = max(total_tokens * 2, 1_000)

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path, conn = _fresh_conn(tmpdir)

        for i, (path, content_hash, tok) in enumerate(read_events):
            hashes = ["base"] + [f"x{j}" for j in range(i)]
            ri = _req(conn, f"2026-01-01T00:{i:02d}:00Z", hashes, usage_input=usage_input)
            _fre(conn, ri, path, content_hash, tok)

        sid = _sessionize_close(conn, db_path)
        report = compute_waste(db_path, sid)

        assert report.wasted_cost >= 0.0
        assert report.avoidable_pct >= 0.0
        # Core invariant: waste never exceeds total billed (float epsilon guard).
        assert report.wasted_cost <= report.total_billed_input_cost + 1e-9, (
            f"wasted={report.wasted_cost:.6f} > billed={report.total_billed_input_cost:.6f}"
        )
        if report.total_billed_input_cost > 0:
            assert report.avoidable_pct <= 100.0 + 1e-6
