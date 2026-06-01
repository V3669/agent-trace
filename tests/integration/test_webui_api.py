"""Integration tests for the read-only web UI API.

Uses Starlette's TestClient so the Starlette app runs in-process.
DuckDB attaches the test SQLite file and reads from it — no mocking of DB.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from agenttrace.webui.api import create_api_app


@pytest.fixture()
def client(tmp_db: tuple[Path, object]) -> TestClient:
    db_path, _ = tmp_db
    app = create_api_app(db_path)
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture()
def client_with_session(
    session_with_rerereads: tuple[Path, str],
) -> tuple[TestClient, str]:
    db_path, session_id = session_with_rerereads
    app = create_api_app(db_path)
    return TestClient(app, raise_server_exceptions=True), session_id


class TestHealth:
    def test_returns_ok(self, client: TestClient) -> None:
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


class TestSessionsList:
    def test_empty_db_returns_empty_list(self, client: TestClient) -> None:
        r = client.get("/api/sessions")
        assert r.status_code == 200
        assert r.json() == []

    def test_sessions_listed(self, client_with_session: tuple[TestClient, str]) -> None:
        client, session_id = client_with_session
        r = client.get("/api/sessions")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 1
        assert data[0]["session_id"] == session_id

    def test_session_summary_fields(self, client_with_session: tuple[TestClient, str]) -> None:
        client, _ = client_with_session
        r = client.get("/api/sessions")
        s = r.json()[0]
        for field in ("session_id", "start_ts", "end_ts", "total_requests", "agent_id", "provider"):
            assert field in s, f"Missing field: {field}"


class TestSessionDetail:
    def test_returns_waste_report(self, client_with_session: tuple[TestClient, str]) -> None:
        client, session_id = client_with_session
        r = client.get(f"/api/sessions/{session_id}")
        assert r.status_code == 200
        data = r.json()
        assert data["session_id"] == session_id
        assert "avoidable_pct" in data
        assert "total_billed_input_cost" in data

    def test_nonexistent_session_returns_report_with_zero_cost(self, client: TestClient) -> None:
        """A session_id that has no events should return a zeroed WasteReport."""
        r = client.get("/api/sessions/nonexistent")
        assert r.status_code == 200
        data = r.json()
        assert data["total_billed_input_cost"] == 0.0


class TestSessionTurns:
    def test_returns_list(self, client_with_session: tuple[TestClient, str]) -> None:
        client, session_id = client_with_session
        r = client.get(f"/api/sessions/{session_id}/turns")
        assert r.status_code == 200
        turns = r.json()
        assert isinstance(turns, list)
        assert len(turns) > 0

    def test_turn_fields_present(self, client_with_session: tuple[TestClient, str]) -> None:
        client, session_id = client_with_session
        r = client.get(f"/api/sessions/{session_id}/turns")
        turn = r.json()[0]
        for field in (
            "request_id",
            "recv_ts",
            "model",
            "input_tokens",
            "output_tokens",
            "total_cost",
            "cause_label",
            "rank",
        ):
            assert field in turn, f"Missing field: {field}"

    def test_limit_param(self, client_with_session: tuple[TestClient, str]) -> None:
        client, session_id = client_with_session
        r = client.get(f"/api/sessions/{session_id}/turns?limit=1")
        assert r.status_code == 200
        assert len(r.json()) == 1

    def test_limit_invalid_falls_back_to_default(
        self, client_with_session: tuple[TestClient, str]
    ) -> None:
        client, session_id = client_with_session
        r = client.get(f"/api/sessions/{session_id}/turns?limit=abc")
        assert r.status_code == 200  # Should not crash

    def test_ranks_are_unique(self, client_with_session: tuple[TestClient, str]) -> None:
        client, session_id = client_with_session
        turns = client.get(f"/api/sessions/{session_id}/turns").json()
        ranks = [t["rank"] for t in turns]
        assert len(ranks) == len(set(ranks))


class TestSessionGrowth:
    def test_returns_list(self, client_with_session: tuple[TestClient, str]) -> None:
        client, session_id = client_with_session
        r = client.get(f"/api/sessions/{session_id}/growth")
        assert r.status_code == 200
        growth = r.json()
        assert isinstance(growth, list)
        assert len(growth) > 0

    def test_growth_fields_present(self, client_with_session: tuple[TestClient, str]) -> None:
        client, session_id = client_with_session
        r = client.get(f"/api/sessions/{session_id}/growth")
        point = r.json()[0]
        for field in (
            "request_index",
            "recv_ts",
            "input_tokens",
            "cumulative_billed_cost",
            "usage_source",
        ):
            assert field in point, f"Missing field: {field}"

    def test_cumulative_cost_non_decreasing(
        self, client_with_session: tuple[TestClient, str]
    ) -> None:
        client, session_id = client_with_session
        growth = client.get(f"/api/sessions/{session_id}/growth").json()
        costs = [g["cumulative_billed_cost"] for g in growth]
        for a, b in zip(costs, costs[1:], strict=False):
            assert b >= a

    def test_empty_session_returns_empty(self, client: TestClient) -> None:
        r = client.get("/api/sessions/no-session/growth")
        assert r.status_code == 200
        assert r.json() == []


class TestLatestSession:
    def test_empty_db_returns_null(self, client: TestClient) -> None:
        r = client.get("/api/sessions/latest")
        assert r.status_code == 200
        assert r.json()["session_id"] is None

    def test_returns_session_id(self, client_with_session: tuple[TestClient, str]) -> None:
        client, session_id = client_with_session
        r = client.get("/api/sessions/latest")
        assert r.status_code == 200
        assert r.json()["session_id"] == session_id
