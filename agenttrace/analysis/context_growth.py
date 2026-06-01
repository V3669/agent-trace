"""Context-growth curve computation (P1 analytics).

Produces a per-request time series showing how much context was billed
and how that context grew over the session.  Used by the React SPA to
render the growth-curve chart (FR-2.2).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import structlog

from agenttrace.analysis.queries import open_duckdb
from agenttrace.pricing.loader import get_price

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class ContextGrowthPoint:
    """One data point in the context-growth curve for a session."""

    request_index: int
    """Zero-based ordinal of this request within the session."""
    recv_ts: str
    """ISO-8601 timestamp of the request."""
    model: str | None
    input_tokens: int
    """Total provider-reported input tokens (includes cached)."""
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    plain_input_tokens: int
    """Non-cached input tokens (input_tokens - cache_read - cache_creation)."""
    billed_input_cost: float
    """Cost of input tokens at their effective billed rates."""
    cumulative_billed_cost: float
    """Running total of billed input cost up to and including this request."""
    usage_source: str
    """'reported' | 'partial' | 'estimated' — confidence indicator for the UI."""


def compute_context_growth(db_path: Path, session_id: str) -> list[ContextGrowthPoint]:
    """Compute context-growth data points for a session.

    Requests are returned in recv_ts order.  Points with unpriced models
    still appear; their cost fields are 0.0 so charts show gaps correctly.

    Args:
        db_path: SQLite store path.
        session_id: Session to analyse.

    Returns:
        Ordered list of ContextGrowthPoint, one per request.
    """
    with open_duckdb(db_path) as duck:
        rows = duck.execute(
            """
            SELECT
                r.recv_ts,
                r.model,
                COALESCE(r.usage_input, 0)          AS input_tokens,
                COALESCE(r.usage_output, 0)         AS output_tokens,
                COALESCE(r.cache_read_input, 0)     AS cache_read,
                COALESCE(r.cache_creation_input, 0) AS cache_creation,
                COALESCE(r.usage_source, 'reported') AS usage_source
            FROM s.requests r
            JOIN s.session_events se ON se.request_id = r.id
            WHERE se.session_id = ?
            ORDER BY r.recv_ts
            """,
            [session_id],
        ).fetchall()

    points: list[ContextGrowthPoint] = []
    cumulative = 0.0

    for idx, row in enumerate(rows):
        recv_ts, model, input_tokens, output_tokens, cache_read, cache_creation, usage_source = row
        price = get_price(model or "") if model else None
        plain_input = max(0, input_tokens - cache_read - cache_creation)

        if price is not None:
            billed_cost = (
                plain_input * price.price_input
                + cache_read * price.price_cache_read
                + cache_creation * price.price_cache_write
            )
        else:
            billed_cost = 0.0

        cumulative += billed_cost

        points.append(
            ContextGrowthPoint(
                request_index=idx,
                recv_ts=recv_ts,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read_tokens=cache_read,
                cache_creation_tokens=cache_creation,
                plain_input_tokens=plain_input,
                billed_input_cost=billed_cost,
                cumulative_billed_cost=cumulative,
                usage_source=usage_source,
            )
        )

    return points
