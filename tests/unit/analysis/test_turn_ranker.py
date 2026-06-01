"""Unit tests for turn_ranker.rank_turns."""

from __future__ import annotations

from pathlib import Path

from agenttrace.analysis.turn_ranker import _determine_cause, rank_turns
from tests.conftest import (
    _insert_request,
    _insert_session_event,
)


class TestRankTurns:
    def test_empty_session_returns_empty(
        self, tmp_db: tuple[Path, object]
    ) -> None:
        db_path, _ = tmp_db
        result = rank_turns(db_path, "no-session")
        assert result == []

    def test_single_request_rank_one(
        self, tmp_db: tuple[Path, object]
    ) -> None:
        db_path, conn = tmp_db
        r1 = _insert_request(
            conn, recv_ts="2026-01-01T00:00:01Z", usage_input=1000, usage_output=100
        )
        _insert_session_event(conn, session_id="sess-single", request_id=r1, event_type="start")

        turns = rank_turns(db_path, "sess-single")
        assert len(turns) == 1
        assert turns[0].rank == 1

    def test_higher_cost_turn_ranked_first(
        self, tmp_db: tuple[Path, object]
    ) -> None:
        db_path, conn = tmp_db
        # r1 is cheaper, r2 is more expensive
        r1 = _insert_request(
            conn, recv_ts="2026-01-01T00:00:01Z", usage_input=100, usage_output=10
        )
        r2 = _insert_request(
            conn, recv_ts="2026-01-01T00:00:02Z", usage_input=5000, usage_output=500
        )
        _insert_session_event(conn, session_id="sess-rank", request_id=r1, event_type="start")
        _insert_session_event(conn, session_id="sess-rank", request_id=r2, event_type="continue")

        turns = rank_turns(db_path, "sess-rank")
        assert turns[0].request_id == r2
        assert turns[0].rank == 1
        assert turns[1].request_id == r1
        assert turns[1].rank == 2

    def test_limit_respected(
        self, tmp_db: tuple[Path, object]
    ) -> None:
        db_path, conn = tmp_db
        reqs = []
        for i in range(10):
            r = _insert_request(
                conn,
                recv_ts=f"2026-01-01T00:00:{i:02d}Z",
                usage_input=1000,
            )
            reqs.append(r)
            _insert_session_event(
                conn,
                session_id="sess-limit",
                request_id=r,
                event_type="start" if i == 0 else "continue",
            )

        turns = rank_turns(db_path, "sess-limit", limit=3)
        assert len(turns) == 3

    def test_reread_cause_label(
        self, session_with_rerereads: tuple[Path, str]
    ) -> None:
        db_path, session_id = session_with_rerereads
        turns = rank_turns(db_path, session_id)
        # The requests that contain the re-read file should be labelled.
        reread_turns = [t for t in turns if t.cause_label == "re-read"]
        assert len(reread_turns) >= 1

    def test_all_ranks_unique(
        self, tmp_db: tuple[Path, object]
    ) -> None:
        db_path, conn = tmp_db
        for i in range(5):
            r = _insert_request(
                conn,
                recv_ts=f"2026-01-01T00:00:{i:02d}Z",
                usage_input=1000 + i * 100,
            )
            _insert_session_event(
                conn,
                session_id="sess-ranks",
                request_id=r,
                event_type="start" if i == 0 else "continue",
            )

        turns = rank_turns(db_path, "sess-ranks")
        ranks = [t.rank for t in turns]
        assert len(ranks) == len(set(ranks))

    def test_turn_cost_fields_non_negative(
        self, session_with_rerereads: tuple[Path, str]
    ) -> None:
        db_path, session_id = session_with_rerereads
        for turn in rank_turns(db_path, session_id):
            assert turn.billed_input_cost >= 0.0
            assert turn.billed_output_cost >= 0.0
            assert turn.total_cost >= 0.0


class TestDetermineCause:
    """Unit tests for the pure _determine_cause helper (no DB required)."""

    def test_reread_wins_over_all(self) -> None:
        cause = _determine_cause(
            request_id=99,
            input_tokens=10000,
            cache_read=9000,
            tool_output_tokens=5000,
            file_read_count=10,
            reread_ids=frozenset({99}),
        )
        assert cause == "re-read"

    def test_large_tool_output(self) -> None:
        cause = _determine_cause(
            request_id=1,
            input_tokens=3000,
            cache_read=0,
            tool_output_tokens=3000,
            file_read_count=2,
            reread_ids=frozenset(),
        )
        assert cause == "large-tool-output"

    def test_context_bloat(self) -> None:
        cause = _determine_cause(
            request_id=1,
            input_tokens=1000,
            cache_read=900,  # 90% cached → bloat
            tool_output_tokens=0,
            file_read_count=0,
            reread_ids=frozenset(),
        )
        assert cause == "context-bloat"

    def test_scan_from_many_reads(self) -> None:
        cause = _determine_cause(
            request_id=1,
            input_tokens=1000,
            cache_read=0,
            tool_output_tokens=0,
            file_read_count=5,
            reread_ids=frozenset(),
        )
        assert cause == "scan"

    def test_normal_when_nothing_triggers(self) -> None:
        cause = _determine_cause(
            request_id=1,
            input_tokens=200,
            cache_read=10,
            tool_output_tokens=0,
            file_read_count=1,
            reread_ids=frozenset(),
        )
        assert cause == "normal"

    def test_zero_input_tokens_no_div_zero(self) -> None:
        cause = _determine_cause(
            request_id=1,
            input_tokens=0,
            cache_read=0,
            tool_output_tokens=0,
            file_read_count=0,
            reread_ids=frozenset(),
        )
        assert cause == "normal"
