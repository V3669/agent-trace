"""Unit tests for report/exporter.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agenttrace.models import WasteReport
from agenttrace.report.exporter import (
    OTEL_CONVENTION_VERSION,
    export_json,
    export_markdown,
    export_otel,
)


@pytest.fixture()
def waste() -> WasteReport:
    return WasteReport(
        session_id="sess-export-test",
        total_billed_input_cost=0.0123,
        wasted_cost=0.0045,
        avoidable_pct=36.6,
        fixed_overhead_cost=0.001,
        total_requests=5,
        wasted_requests=2,
    )


class TestExportJson:
    def test_valid_json(self, waste: WasteReport, tmp_path: Path) -> None:
        out = tmp_path / "out.json"
        export_json(waste, out)
        data = json.loads(out.read_text())
        assert data["session_id"] == "sess-export-test"
        assert data["avoidable_pct"] == pytest.approx(36.6, rel=1e-3)
        assert data["total_requests"] == 5

    def test_all_fields_present(self, waste: WasteReport, tmp_path: Path) -> None:
        out = tmp_path / "out.json"
        export_json(waste, out)
        data = json.loads(out.read_text())
        for field in WasteReport.model_fields:
            assert field in data


class TestExportMarkdown:
    def test_contains_session_id(self, waste: WasteReport, tmp_path: Path) -> None:
        out = tmp_path / "out.md"
        export_markdown(waste, out)
        text = out.read_text()
        assert "sess-export-test" in text

    def test_contains_avoidable_pct(self, waste: WasteReport, tmp_path: Path) -> None:
        out = tmp_path / "out.md"
        export_markdown(waste, out)
        text = out.read_text()
        assert "36.6%" in text

    def test_methodology_note_present(self, waste: WasteReport, tmp_path: Path) -> None:
        out = tmp_path / "out.md"
        export_markdown(waste, out)
        text = out.read_text()
        assert "Methodology" in text
        assert "conservative" in text.lower()


class TestExportOtel:
    def test_valid_json(
        self,
        session_with_rerereads: tuple[Path, str],
        tmp_path: Path,
    ) -> None:
        db_path, session_id = session_with_rerereads
        out = tmp_path / "out.otel.json"
        export_otel(db_path, session_id, out)
        data = json.loads(out.read_text())
        assert "resourceSpans" in data

    def test_schema_version_pinned(
        self,
        session_with_rerereads: tuple[Path, str],
        tmp_path: Path,
    ) -> None:
        db_path, session_id = session_with_rerereads
        out = tmp_path / "out.otel.json"
        export_otel(db_path, session_id, out)
        data = json.loads(out.read_text())
        assert data["schemaVersion"] == OTEL_CONVENTION_VERSION

    def test_spans_contain_gen_ai_attrs(
        self,
        session_with_rerereads: tuple[Path, str],
        tmp_path: Path,
    ) -> None:
        db_path, session_id = session_with_rerereads
        out = tmp_path / "out.otel.json"
        export_otel(db_path, session_id, out)
        data = json.loads(out.read_text())
        spans = data["resourceSpans"][0]["scopeSpans"][0]["spans"]
        assert len(spans) > 0
        # Check that every span has gen_ai.system in its attributes.
        for span in spans:
            keys = [kv["key"] for kv in span["attributes"]]
            assert "gen_ai.system" in keys

    def test_session_id_in_document(
        self,
        session_with_rerereads: tuple[Path, str],
        tmp_path: Path,
    ) -> None:
        db_path, session_id = session_with_rerereads
        out = tmp_path / "out.otel.json"
        export_otel(db_path, session_id, out)
        data = json.loads(out.read_text())
        assert data["sessionId"] == session_id

    def test_empty_session_produces_no_spans(
        self,
        tmp_db: tuple[Path, object],
        tmp_path: Path,
    ) -> None:
        db_path, _ = tmp_db
        out = tmp_path / "out.otel.json"
        export_otel(db_path, "empty-session", out)
        data = json.loads(out.read_text())
        spans = data["resourceSpans"][0]["scopeSpans"][0]["spans"]
        assert spans == []
