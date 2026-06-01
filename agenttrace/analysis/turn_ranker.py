"""Turn ranking with heuristic cause labelling (P1 analytics, FR-2.6).

Ranks requests within a session by total billed cost (descending) and
attaches a heuristic cause label to each:

- ``re-read``          — request contains a file read of content already seen
                         this session (identical path + content_hash).
- ``large-tool-output``— large aggregate tool-output payload (> threshold).
- ``context-bloat``    — cache_read_tokens dominate input (> 80% fraction).
- ``scan``             — many file reads in one request (likely glob/grep result).
- ``normal``           — no notable waste pattern detected.

Labels are heuristics, not guarantees.  They guide UI triage, not billing.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import structlog

from agenttrace.analysis.queries import open_duckdb
from agenttrace.pricing.loader import get_price

logger = structlog.get_logger(__name__)

# Heuristic thresholds — not hardcoded in the public API so tests can override.
_LARGE_TOOL_OUTPUT_TOKENS: int = 2_000
_CONTEXT_BLOAT_FRACTION: float = 0.80
_SCAN_READ_COUNT: int = 3


@dataclass(frozen=True)
class TurnRank:
    """One ranked entry in the session turn list."""

    request_id: int
    recv_ts: str
    model: str | None
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    billed_input_cost: float
    billed_output_cost: float
    total_cost: float
    cause_label: str
    usage_source: str
    rank: int


def rank_turns(
    db_path: Path,
    session_id: str,
    limit: int = 50,
) -> list[TurnRank]:
    """Return turns ranked by total billed cost, highest first.

    Args:
        db_path: SQLite store path.
        session_id: Session to rank.
        limit: Maximum number of turns to return.

    Returns:
        List of TurnRank, ranked 1..N by descending total cost.
    """
    with open_duckdb(db_path) as duck:
        rows = duck.execute(
            """
            SELECT
                r.id                                            AS request_id,
                r.recv_ts,
                r.model,
                COALESCE(r.usage_input, 0)                     AS input_tokens,
                COALESCE(r.usage_output, 0)                    AS output_tokens,
                COALESCE(r.cache_read_input, 0)                AS cache_read,
                COALESCE(r.cache_creation_input, 0)            AS cache_creation,
                COALESCE(r.usage_source, 'reported')           AS usage_source,
                COALESCE(
                    (SELECT SUM(fre.approx_tokens)
                     FROM s.file_read_events fre
                     WHERE fre.request_id = r.id),
                    0
                )                                              AS tool_output_tokens,
                COALESCE(
                    (SELECT COUNT(*)
                     FROM s.file_read_events fre
                     WHERE fre.request_id = r.id),
                    0
                )                                              AS file_read_count
            FROM s.requests r
            JOIN s.session_events se ON se.request_id = r.id
            WHERE se.session_id = ?
            ORDER BY r.recv_ts
            """,
            [session_id],
        ).fetchall()

    reread_ids = _get_reread_request_ids(db_path, session_id)

    unranked: list[tuple[float, TurnRank]] = []
    for row in rows:
        (
            request_id,
            recv_ts,
            model,
            input_tokens,
            output_tokens,
            cache_read,
            cache_creation,
            usage_source,
            tool_output_tokens,
            file_read_count,
        ) = row

        price = get_price(model or "") if model else None
        plain_input = max(0, input_tokens - cache_read - cache_creation)

        if price is not None:
            billed_input = (
                plain_input * price.price_input
                + cache_read * price.price_cache_read
                + cache_creation * price.price_cache_write
            )
            billed_output = output_tokens * price.price_output
        else:
            billed_input = 0.0
            billed_output = 0.0

        total = billed_input + billed_output
        cause = _determine_cause(
            request_id=int(request_id),
            input_tokens=int(input_tokens),
            cache_read=int(cache_read),
            tool_output_tokens=int(tool_output_tokens),
            file_read_count=int(file_read_count),
            reread_ids=reread_ids,
        )
        unranked.append(
            (
                total,
                TurnRank(
                    request_id=int(request_id),
                    recv_ts=recv_ts,
                    model=model,
                    input_tokens=int(input_tokens),
                    output_tokens=int(output_tokens),
                    cache_read_tokens=int(cache_read),
                    cache_creation_tokens=int(cache_creation),
                    billed_input_cost=billed_input,
                    billed_output_cost=billed_output,
                    total_cost=total,
                    cause_label=cause,
                    usage_source=usage_source,
                    rank=0,  # placeholder
                ),
            )
        )

    unranked.sort(key=lambda pair: pair[0], reverse=True)

    return [
        replace(turn, rank=i + 1)
        for i, (_cost, turn) in enumerate(unranked[:limit])
    ]


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _get_reread_request_ids(db_path: Path, session_id: str) -> frozenset[int]:
    """Return the set of request IDs that contain at least one re-read file."""
    with open_duckdb(db_path) as duck:
        rows = duck.execute(
            """
            SELECT DISTINCT fre.request_id
            FROM s.file_read_events fre
            JOIN s.requests r ON r.id = fre.request_id
            JOIN s.session_events se ON se.request_id = r.id
            WHERE se.session_id = ?
              AND (fre.path, fre.content_hash) IN (
                  SELECT fre2.path, fre2.content_hash
                  FROM s.file_read_events fre2
                  JOIN s.requests r2 ON r2.id = fre2.request_id
                  JOIN s.session_events se2 ON se2.request_id = r2.id
                  WHERE se2.session_id = ?
                  GROUP BY fre2.path, fre2.content_hash
                  HAVING COUNT(*) > 1
              )
            """,
            [session_id, session_id],
        ).fetchall()
    return frozenset(int(row[0]) for row in rows)


def _determine_cause(
    *,
    request_id: int,
    input_tokens: int,
    cache_read: int,
    tool_output_tokens: int,
    file_read_count: int,
    reread_ids: frozenset[int],
) -> str:
    if request_id in reread_ids:
        return "re-read"
    if file_read_count > 0 and tool_output_tokens > _LARGE_TOOL_OUTPUT_TOKENS:
        return "large-tool-output"
    if input_tokens > 0 and cache_read / input_tokens > _CONTEXT_BLOAT_FRACTION:
        return "context-bloat"
    if file_read_count >= _SCAN_READ_COUNT:
        return "scan"
    return "normal"
