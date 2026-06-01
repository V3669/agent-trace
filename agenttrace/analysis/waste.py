"""DuckDB-based waste computation.

Attaches the SQLite store read-only and computes the cache-net waste metrics
defined in PLAN.md Appendix C.  All writes remain in SQLite; DuckDB is pure
analytics.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from agenttrace.analysis.queries import open_duckdb
from agenttrace.models import SessionSummary, WasteReport
from agenttrace.pricing.loader import get_price

logger = structlog.get_logger(__name__)


def compute_waste(db_path: Path, session_id: str) -> WasteReport:
    """Compute waste metrics for a session using DuckDB over the SQLite store.

    Only requests with usage_source='reported' are included in the headline
    reconciliation.  Partial/estimated turns are excluded from ±1% gate
    (see PLAN.md §8.5).

    Args:
        db_path: Path to the SQLite store.
        session_id: Session to analyse.

    Returns:
        WasteReport with billed cost, wasted cost, avoidable_pct, etc.
    """
    with open_duckdb(db_path) as duck:
        # Fetch all reported requests in the session.
        requests = duck.execute(
            """
            SELECT
                r.id,
                r.model,
                COALESCE(r.usage_input, 0)          AS usage_input,
                COALESCE(r.usage_output, 0)         AS usage_output,
                COALESCE(r.cache_read_input, 0)     AS cache_read,
                COALESCE(r.cache_creation_input, 0) AS cache_create,
                r.usage_source
            FROM s.requests r
            JOIN s.session_events se ON se.request_id = r.id
            WHERE se.session_id = ?
              AND r.usage_source = 'reported'
            ORDER BY r.recv_ts
            """,
            [session_id],
        ).fetchall()

        total_billed_input_cost = 0.0
        fixed_overhead_cost = 0.0

        for row in requests:
            _req_id, model, u_in, _u_out, cache_read, cache_create, _source = row
            price = get_price(model or "") if model else None
            if price is None:
                continue
            plain_input = max(0, u_in - cache_read - cache_create)
            billed = (
                plain_input * price.price_input
                + cache_read * price.price_cache_read
                + cache_create * price.price_cache_write
            )
            total_billed_input_cost += billed

        # Re-read waste: identical (path, content_hash) appearing >1× in session.
        rerereads = duck.execute(
            """
            SELECT
                fre.path,
                fre.content_hash,
                COUNT(*)               AS read_count,
                SUM(fre.approx_tokens) AS total_tokens,
                r.model
            FROM s.file_read_events fre
            JOIN s.requests r ON r.id = fre.request_id
            JOIN s.session_events se ON se.request_id = r.id
            WHERE se.session_id = ?
            GROUP BY fre.path, fre.content_hash, r.model
            HAVING COUNT(*) > 1
            """,
            [session_id],
        ).fetchall()

        wasted_cost = 0.0
        wasted_requests = 0
        seen_paths: set[str] = set()

        for path, _content_hash, read_count, total_tokens, model in rerereads:
            price = get_price(model or "") if model else None
            if price is None:
                continue
            # Re-reads billed at cache-read rate (conservative, per D7).
            # Attribute only the *repeat* reads: total_tokens minus one share
            # (the first, legitimate read).  Integer ceiling division ensures
            # we err on the side of under-counting waste, not over-counting.
            # For N reads: repeat_tokens = total − floor(total/N).
            repeat_tokens = total_tokens - (total_tokens // read_count)
            wasted_cost += repeat_tokens * price.price_cache_read
            if path not in seen_paths:
                wasted_requests += 1
                seen_paths.add(path)

        avoidable_pct = (
            (wasted_cost / total_billed_input_cost * 100) if total_billed_input_cost > 0 else 0.0
        )

        count_row = duck.execute(
            "SELECT COUNT(*) FROM s.session_events WHERE session_id = ?",
            [session_id],
        ).fetchone()
        total_requests: int = int(count_row[0]) if count_row else 0

    return WasteReport(
        session_id=session_id,
        total_billed_input_cost=total_billed_input_cost,
        wasted_cost=wasted_cost,
        avoidable_pct=avoidable_pct,
        fixed_overhead_cost=fixed_overhead_cost,
        total_requests=total_requests,
        wasted_requests=wasted_requests,
    )


def list_sessions(db_path: Path) -> list[SessionSummary]:
    """Return a summary of all sessions in the store, newest first.

    Args:
        db_path: Path to the SQLite store.

    Returns:
        List of SessionSummary ordered by start_ts descending.
    """
    if not db_path.exists():
        return []

    with open_duckdb(db_path) as duck:
        rows = duck.execute(
            """
            SELECT
                se.session_id,
                MIN(r.recv_ts)   AS start_ts,
                MAX(r.recv_ts)   AS end_ts,
                COUNT(r.id)      AS total_requests,
                -- Use the most-frequent agent/provider as the session label.
                MODE(r.agent_id)   AS agent_id,
                MODE(r.provider)   AS provider
            FROM s.session_events se
            JOIN s.requests r ON r.id = se.request_id
            GROUP BY se.session_id
            ORDER BY start_ts DESC
            """
        ).fetchall()

    summaries: list[SessionSummary] = []
    for session_id, start_ts, end_ts, total_requests, agent_id, provider in rows:
        summaries.append(
            SessionSummary(
                session_id=session_id,
                start_ts=start_ts or "",
                end_ts=end_ts or "",
                total_requests=int(total_requests),
                agent_id=agent_id or "unknown",
                provider=provider or "unknown",
            )
        )
    return summaries


def get_latest_session_id(db_path: Path) -> str | None:
    """Return the session_id of the most recent session, or None if empty."""
    if not db_path.exists():
        return None

    with open_duckdb(db_path) as duck:
        row = duck.execute(
            "SELECT session_id FROM s.session_events ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return row[0] if row else None
