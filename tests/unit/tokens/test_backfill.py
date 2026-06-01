"""Unit tests for tokens/backfill.py."""

from __future__ import annotations

import json
import sqlite3

import pytest

from agenttrace.models import EstimateConfidence, UsageSource
from agenttrace.tokens.backfill import _estimate_from_body, backfill_partial_turns
from tests.conftest import _apply_schema, _insert_request


@pytest.fixture()
def mem_conn() -> sqlite3.Connection:
    """In-memory SQLite connection with schema applied."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys=ON")
    _apply_schema(conn)
    return conn


class TestBackfillPartialTurns:
    def test_partial_request_gets_estimate(self, mem_conn: sqlite3.Connection) -> None:
        body = json.dumps(
            {"messages": [{"role": "user", "content": "Hello world, what is the plan?"}]}
        ).encode()
        req_id = _insert_request(
            mem_conn,
            usage_source="partial",
            usage_input=0,
            body_blob=body,
        )

        count = backfill_partial_turns(mem_conn)
        assert count == 1

        row = mem_conn.execute(
            "SELECT usage_input, usage_source, estimate_confidence FROM requests WHERE id = ?",
            (req_id,),
        ).fetchone()
        assert row[0] > 0, "Expected token estimate > 0"
        assert row[1] == UsageSource.estimated
        assert row[2] == EstimateConfidence.low

    def test_reported_request_untouched(self, mem_conn: sqlite3.Connection) -> None:
        body = json.dumps({"messages": [{"role": "user", "content": "hi"}]}).encode()
        req_id = _insert_request(
            mem_conn,
            usage_source="reported",
            usage_input=999,
            body_blob=body,
        )

        count = backfill_partial_turns(mem_conn)
        assert count == 0

        row = mem_conn.execute(
            "SELECT usage_input, usage_source FROM requests WHERE id = ?", (req_id,)
        ).fetchone()
        assert row[0] == 999
        assert row[1] == "reported"

    def test_no_body_blob_skipped(self, mem_conn: sqlite3.Connection) -> None:
        _insert_request(
            mem_conn,
            usage_source="partial",
            usage_input=0,
            body_blob=None,
        )
        count = backfill_partial_turns(mem_conn)
        assert count == 0

    def test_multiple_partial_requests_all_backfilled(
        self, mem_conn: sqlite3.Connection
    ) -> None:
        body = json.dumps({"messages": [{"role": "user", "content": "test"}]}).encode()
        for _ in range(3):
            _insert_request(
                mem_conn,
                usage_source="partial",
                usage_input=0,
                body_blob=body,
            )
        count = backfill_partial_turns(mem_conn)
        assert count == 3

    def test_empty_table_returns_zero(self, mem_conn: sqlite3.Connection) -> None:
        count = backfill_partial_turns(mem_conn)
        assert count == 0


class TestEstimateFromBody:
    def test_string_content(self) -> None:
        body = json.dumps(
            {"messages": [{"role": "user", "content": "Hello world"}]}
        ).encode()
        tokens = _estimate_from_body(body)
        assert tokens > 0

    def test_list_content(self) -> None:
        body = json.dumps(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": "Hello world"}],
                    }
                ]
            }
        ).encode()
        tokens = _estimate_from_body(body)
        assert tokens > 0

    def test_system_prompt_counted(self) -> None:
        body_with_system = json.dumps(
            {
                "system": "You are a helpful AI.",
                "messages": [{"role": "user", "content": "Hi"}],
            }
        ).encode()
        body_without_system = json.dumps(
            {"messages": [{"role": "user", "content": "Hi"}]}
        ).encode()
        assert _estimate_from_body(body_with_system) > _estimate_from_body(body_without_system)

    def test_malformed_json_returns_zero(self) -> None:
        assert _estimate_from_body(b"not json") == 0

    def test_empty_messages_returns_zero(self) -> None:
        body = json.dumps({"messages": []}).encode()
        assert _estimate_from_body(body) == 0
