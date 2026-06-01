"""Multi-format session exporter (P1, task 12).

Supported formats:
  - ``json`` — WasteReport as JSON.
  - ``md``   — Markdown summary with methodology note.
  - ``otel`` — gen_ai.* OTel span format (draft convention, version-pinned).

The OTel format maps each session to a top-level ResourceSpan with one
InstrumentationScope named ``agenttrace`` and a Span per request carrying
``gen_ai.*`` attributes.  The convention version is pinned via the
``schema_version`` field so downstream consumers can detect convention drift
(D3, D13).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from agenttrace.analysis.queries import open_duckdb
from agenttrace.models import WasteReport

logger = structlog.get_logger(__name__)

# Pin to the OTel GenAI semantic conventions draft in use.
# Update this when bumping convention support; record in schema_meta on export.
OTEL_CONVENTION_VERSION = "1.27.0-draft"


def export_json(waste: WasteReport, out_path: Path) -> None:
    """Write WasteReport as indented JSON."""
    out_path.write_text(waste.model_dump_json(indent=2), encoding="utf-8")


def export_markdown(waste: WasteReport, out_path: Path) -> None:
    """Write a human-readable Markdown summary of the waste report."""
    lines = [
        f"# AgentTrace Export — {waste.session_id}",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Avoidable waste | {waste.avoidable_pct:.1f}% |",
        f"| Total billed input cost | ${waste.total_billed_input_cost:.6f} |",
        f"| Wasted cost | ${waste.wasted_cost:.6f} |",
        f"| Fixed overhead cost | ${waste.fixed_overhead_cost:.6f} |",
        f"| Total requests | {waste.total_requests} |",
        f"| Re-read files | {waste.wasted_requests} |",
        "",
        "## Methodology",
        "",
        "Waste = Σ(re-read identical file content) × cache-read billed rate.",
        "Conservative: only provably identical re-sent content (same path + SHA-256) counts.",
        f"Definition: PLAN.md Appendix C. Convention: {OTEL_CONVENTION_VERSION}.",
    ]
    out_path.write_text("\n".join(lines), encoding="utf-8")


def export_otel(db_path: Path, session_id: str, out_path: Path) -> None:
    """Export session as gen_ai.* OTel ResourceSpans JSON.

    Each request in the session becomes one Span with gen_ai.* attributes
    for model, token usage (input/output/cache), and provider.  The
    session-level waste summary is attached to the ResourceSpan attributes.

    The output is a single JSON document whose schema mirrors the OTel
    OTLP/JSON ExportTraceServiceRequest but is simplified for readability.

    Args:
        db_path: SQLite store path (used to read per-request data).
        session_id: Session to export.
        out_path: Destination file.
    """
    with open_duckdb(db_path) as duck:
        # Session-level metadata
        session_row = duck.execute(
            """
            SELECT
                MIN(r.recv_ts)  AS start_ts,
                MAX(r.recv_ts)  AS end_ts,
                MODE(r.agent_id)  AS agent_id,
                MODE(r.provider)  AS provider
            FROM s.requests r
            JOIN s.session_events se ON se.request_id = r.id
            WHERE se.session_id = ?
            """,
            [session_id],
        ).fetchone()

        # Per-request spans
        request_rows = duck.execute(
            """
            SELECT
                r.id,
                r.recv_ts,
                r.model,
                COALESCE(r.usage_input, 0)          AS input_tokens,
                COALESCE(r.usage_output, 0)         AS output_tokens,
                COALESCE(r.cache_read_input, 0)     AS cache_read,
                COALESCE(r.cache_creation_input, 0) AS cache_creation,
                r.usage_source,
                r.estimate_confidence,
                r.provider,
                r.agent_id
            FROM s.requests r
            JOIN s.session_events se ON se.request_id = r.id
            WHERE se.session_id = ?
            ORDER BY r.recv_ts
            """,
            [session_id],
        ).fetchall()

    _fallback = ("", "", "unknown", "unknown")
    start_ts, end_ts, agent_id, provider = session_row if session_row else _fallback

    spans: list[dict[str, Any]] = []
    for row in request_rows:
        (
            req_id,
            recv_ts,
            model,
            input_tokens,
            output_tokens,
            cache_read,
            cache_creation,
            usage_source,
            estimate_confidence,
            req_provider,
            req_agent_id,
        ) = row

        span_attrs: dict[str, Any] = {
            "gen_ai.system": req_provider or provider or "unknown",
            "gen_ai.operation.name": "chat",
            "gen_ai.request.model": model or "unknown",
            "gen_ai.usage.input_tokens": int(input_tokens),
            "gen_ai.usage.output_tokens": int(output_tokens),
            "gen_ai.usage.cache_read_input_tokens": int(cache_read),
            "gen_ai.usage.cache_creation_input_tokens": int(cache_creation),
            # AgentTrace extensions (namespaced to avoid collision)
            "agenttrace.agent_id": req_agent_id or agent_id or "unknown",
            "agenttrace.usage_source": usage_source or "reported",
            "agenttrace.session_id": session_id,
            "agenttrace.request_id": int(req_id),
        }
        if estimate_confidence:
            span_attrs["agenttrace.estimate_confidence"] = estimate_confidence

        spans.append(
            {
                "traceId": session_id.replace("s_", "").replace("_", ""),
                "spanId": f"{int(req_id):016x}",
                "name": "gen_ai.chat",
                "startTimeUnixNano": _iso_to_ns(recv_ts),
                "kind": 3,  # CLIENT
                "attributes": _to_otel_kv(span_attrs),
                "status": {"code": 1},  # OK
            }
        )

    document: dict[str, Any] = {
        "schemaVersion": OTEL_CONVENTION_VERSION,
        "sessionId": session_id,
        "resourceSpans": [
            {
                "resource": {
                    "attributes": _to_otel_kv(
                        {
                            "service.name": "agenttrace",
                            "gen_ai.system": provider or "unknown",
                            "agenttrace.agent_id": agent_id or "unknown",
                            "agenttrace.session.start_ts": start_ts or "",
                            "agenttrace.session.end_ts": end_ts or "",
                        }
                    )
                },
                "scopeSpans": [
                    {
                        "scope": {
                            "name": "agenttrace",
                            "version": OTEL_CONVENTION_VERSION,
                        },
                        "spans": spans,
                    }
                ],
            }
        ],
    }

    out_path.write_text(json.dumps(document, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _to_otel_kv(attrs: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert a plain dict to OTel key-value attribute list."""
    result = []
    for key, value in attrs.items():
        if isinstance(value, bool):
            result.append({"key": key, "value": {"boolValue": value}})
        elif isinstance(value, int):
            result.append({"key": key, "value": {"intValue": str(value)}})
        elif isinstance(value, float):
            result.append({"key": key, "value": {"doubleValue": value}})
        else:
            result.append({"key": key, "value": {"stringValue": str(value)}})
    return result


def _iso_to_ns(ts: str) -> int:
    """Convert an ISO-8601 timestamp string to Unix nanoseconds (best-effort).

    Returns 0 on parse failure and logs a warning so corrupt OTel spans are
    visible rather than silently set to epoch.
    """
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return int(dt.replace(tzinfo=UTC).timestamp() * 1_000_000_000)
    except (ValueError, AttributeError):
        logger.warning("export.timestamp_parse_error", ts=ts)
        return 0
