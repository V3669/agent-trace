"""Tiktoken backfill for partial-stream requests (P1, FR task 11).

When a streaming response terminates before the provider sends a usage frame
(e.g. client disconnected, rate-limit abort), the Writer stores the request
with usage_source='partial'.  This module estimates the input-token count
from the stored request body using tiktoken and updates the row in-place.

Safety guarantees:
  - Never modifies rows that already have authoritative usage (reported).
  - Marks every estimate with estimate_confidence='low' (D6).
  - Runs synchronously against a SQLite connection; the caller must own the
    connection and commit strategy.  The Writer calls this during its
    batched-flush phase, off the hot path.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import structlog

from agenttrace.models import EstimateConfidence, UsageSource
from agenttrace.tokens.estimator import estimate_tokens

logger = structlog.get_logger(__name__)


def backfill_partial_turns(conn: sqlite3.Connection) -> int:
    """Estimate token counts for partial-stream requests.

    Reads all requests with usage_source='partial' that have a stored body_blob,
    estimates input tokens from the messages array, and updates usage_input,
    usage_source → 'estimated', and estimate_confidence → 'low'.

    Args:
        conn: Open SQLite connection.  The caller is responsible for committing.

    Returns:
        Number of rows updated.
    """
    cursor = conn.execute(
        "SELECT id, body_blob FROM requests WHERE usage_source = ? AND body_blob IS NOT NULL",
        (UsageSource.partial,),
    )
    rows = cursor.fetchall()

    count = 0
    for request_id, body_blob in rows:
        if not body_blob:
            continue
        try:
            estimated_tokens = _estimate_from_body(body_blob)
            conn.execute(
                """
                UPDATE requests
                SET  usage_input          = ?,
                     usage_source         = ?,
                     estimate_confidence  = ?
                WHERE id             = ?
                  AND usage_source   = ?
                """,
                (
                    estimated_tokens,
                    UsageSource.estimated,
                    EstimateConfidence.low,
                    request_id,
                    UsageSource.partial,
                ),
            )
            count += 1
        except Exception:
            logger.exception("backfill.estimate_error", request_id=request_id)

    return count


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _estimate_from_body(body_blob: bytes) -> int:
    """Parse request body and estimate total input tokens from messages."""
    try:
        body: dict[str, Any] = json.loads(body_blob)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return 0

    total = 0

    # System prompt / Anthropic system blocks
    system_raw = body.get("system", [])
    if isinstance(system_raw, str):
        total += estimate_tokens(system_raw)
    elif isinstance(system_raw, list):
        for block in system_raw:
            if isinstance(block, dict):
                total += estimate_tokens(block.get("text", ""))

    # Messages
    for msg in body.get("messages", []):
        content = msg.get("content", "")
        if isinstance(content, str):
            total += estimate_tokens(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    total += estimate_tokens(block.get("text", ""))

    return total
