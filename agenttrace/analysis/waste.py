from __future__ import annotations

from pathlib import Path

import duckdb
import structlog

from agenttrace.models import WasteReport
from agenttrace.pricing.loader import get_price

logger = structlog.get_logger(__name__)

_DUCKDB_SETUP = """
INSTALL sqlite;
LOAD sqlite;
"""


def compute_waste(db_path: Path, session_id: str) -> WasteReport:
    duck = duckdb.connect(":memory:")
    duck.execute(_DUCKDB_SETUP)
    duck.execute(f"ATTACH '{db_path}' AS s (TYPE sqlite, READ_ONLY)")

    # Get all requests in session
    requests = duck.execute(
        """
        SELECT
            r.id,
            r.model,
            r.usage_input,
            r.usage_output,
            r.cache_read_input,
            r.cache_creation_input,
            r.usage_source
        FROM s.requests r
        JOIN s.session_events se ON se.request_id = r.id
        WHERE se.session_id = ?
          AND r.usage_source IN ('reported')
        """,
        [session_id],
    ).fetchall()

    total_billed_input_cost = 0.0
    fixed_overhead_cost = 0.0

    for row in requests:
        req_id, model, u_in, u_out, cache_read, cache_create, u_source = row
        price = get_price(model or "") if model else None

        if price is None:
            continue

        u_in = u_in or 0
        cache_read = cache_read or 0
        cache_create = cache_create or 0
        plain_input = max(0, u_in - cache_read - cache_create)

        billed = (
            plain_input * price.price_input
            + cache_read * price.price_cache_read
            + cache_create * price.price_cache_write
        )
        total_billed_input_cost += billed

    # Re-read waste: file_read_events with same (path, content_hash) appearing >1 in session
    rerereads = duck.execute(
        """
        SELECT
            fre.path,
            fre.content_hash,
            COUNT(*) as read_count,
            SUM(fre.approx_tokens) as total_tokens,
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
        # Re-reads cost at cache-read rate (conservative); subtract first read
        repeat_tokens = total_tokens - (total_tokens // read_count)
        wasted_cost += repeat_tokens * price.price_cache_read
        if path not in seen_paths:
            wasted_requests += 1
            seen_paths.add(path)

    avoidable_pct = (
        (wasted_cost / total_billed_input_cost * 100) if total_billed_input_cost > 0 else 0.0
    )

    count_row = duck.execute(
        "SELECT COUNT(*) FROM s.session_events WHERE session_id = ?", [session_id]
    ).fetchone()
    total_requests: int = int(count_row[0]) if count_row else 0

    duck.close()

    return WasteReport(
        session_id=session_id,
        total_billed_input_cost=total_billed_input_cost,
        wasted_cost=wasted_cost,
        avoidable_pct=avoidable_pct,
        fixed_overhead_cost=fixed_overhead_cost,
        total_requests=total_requests,
        wasted_requests=wasted_requests,
    )


def get_latest_session_id(db_path: Path) -> str | None:
    duck = duckdb.connect(":memory:")
    duck.execute(_DUCKDB_SETUP)
    duck.execute(f"ATTACH '{db_path}' AS s (TYPE sqlite, READ_ONLY)")
    row = duck.execute(
        "SELECT session_id FROM s.session_events ORDER BY id DESC LIMIT 1"
    ).fetchone()
    duck.close()
    return row[0] if row else None
