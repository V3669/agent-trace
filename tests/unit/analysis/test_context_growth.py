"""Unit tests for context_growth.compute_context_growth."""

from __future__ import annotations

from pathlib import Path

from agenttrace.analysis.context_growth import compute_context_growth
from tests.conftest import (
    _insert_request,
    _insert_session_event,
)


class TestComputeContextGrowth:
    def test_empty_session_returns_empty(self, tmp_db: tuple[Path, object]) -> None:
        db_path, _ = tmp_db
        result = compute_context_growth(db_path, "nonexistent-session")
        assert result == []

    def test_single_request_returns_one_point(self, tmp_db: tuple[Path, object]) -> None:
        db_path, conn = tmp_db
        r1 = _insert_request(
            conn, recv_ts="2026-01-01T00:00:01Z", usage_input=500, usage_output=100
        )
        _insert_session_event(conn, session_id="sess-x", request_id=r1, event_type="start")

        points = compute_context_growth(db_path, "sess-x")
        assert len(points) == 1
        assert points[0].request_index == 0
        assert points[0].input_tokens == 500
        assert points[0].output_tokens == 100

    def test_points_ordered_by_recv_ts(self, tmp_db: tuple[Path, object]) -> None:
        db_path, conn = tmp_db
        r1 = _insert_request(conn, recv_ts="2026-01-01T00:00:01Z", usage_input=100)
        r2 = _insert_request(conn, recv_ts="2026-01-01T00:00:02Z", usage_input=200)
        r3 = _insert_request(conn, recv_ts="2026-01-01T00:00:03Z", usage_input=300)
        for i, r in enumerate([r1, r2, r3], 0):
            _insert_session_event(
                conn,
                session_id="sess-order",
                request_id=r,
                event_type="start" if i == 0 else "continue",
            )

        points = compute_context_growth(db_path, "sess-order")
        assert [p.request_index for p in points] == [0, 1, 2]
        assert [p.input_tokens for p in points] == [100, 200, 300]

    def test_cumulative_cost_increases_monotonically(
        self, session_with_rerereads: tuple[Path, str]
    ) -> None:
        db_path, session_id = session_with_rerereads
        points = compute_context_growth(db_path, session_id)
        costs = [p.cumulative_billed_cost for p in points]
        # Cumulative must be non-decreasing.
        for a, b in zip(costs, costs[1:], strict=False):
            assert b >= a

    def test_plain_input_tokens_correct(self, tmp_db: tuple[Path, object]) -> None:
        db_path, conn = tmp_db
        # 500 total input, 200 cached → plain = 300
        r1 = _insert_request(
            conn,
            recv_ts="2026-01-01T00:00:01Z",
            usage_input=500,
            cache_read_input=200,
        )
        _insert_session_event(conn, session_id="sess-plain", request_id=r1, event_type="start")

        points = compute_context_growth(db_path, "sess-plain")
        assert points[0].plain_input_tokens == 300
        assert points[0].cache_read_tokens == 200

    def test_unpriced_model_cost_is_zero(self, tmp_db: tuple[Path, object]) -> None:
        """Requests with unknown model get billed_cost=0 but still appear."""
        db_path, conn = tmp_db
        r1 = _insert_request(
            conn,
            recv_ts="2026-01-01T00:00:01Z",
            model="nonexistent-model-xyz",
            usage_input=1000,
        )
        _insert_session_event(conn, session_id="sess-nomodel", request_id=r1, event_type="start")

        points = compute_context_growth(db_path, "sess-nomodel")
        assert len(points) == 1
        assert points[0].billed_input_cost == 0.0

    def test_usage_source_propagated(self, tmp_db: tuple[Path, object]) -> None:
        db_path, conn = tmp_db
        r1 = _insert_request(
            conn,
            recv_ts="2026-01-01T00:00:01Z",
            usage_source="partial",
            usage_input=0,
        )
        _insert_session_event(conn, session_id="sess-partial", request_id=r1, event_type="start")

        points = compute_context_growth(db_path, "sess-partial")
        assert points[0].usage_source == "partial"
