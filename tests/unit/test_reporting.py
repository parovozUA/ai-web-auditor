import json
from pathlib import Path

import pytest

from ai_web_auditor.models import (
    AnalysisStatus,
    Finding,
    FindingCategory,
    FindingCode,
    Incident,
    PageObservation,
    PageScanResult,
    ScanStatus,
    Severity,
    TraceEvent,
)
from ai_web_auditor.reporting import ReportRenderer
from ai_web_auditor.workflow import AuditState


@pytest.fixture
def report_state() -> AuditState:
    finding = Finding(
        finding_id="finding-123",
        check_code=FindingCode.TITLE_MISSING,
        category=FindingCategory.SEO,
        source_page="https://example.com/",
        signature="title_missing",
        message="The page title is missing.",
        occurrence_count=1,
    )
    incident = Incident(
        incident_id="incident-456",
        title="Missing Title",
        summary="Title is missing on seed page.",
        severity=Severity.LOW,
        finding_ids=["finding-123"],
        affected_pages=["https://example.com/"],
        remediation="Add a title tag.",
    )
    obs = PageObservation(
        requested_url="https://example.com/",
        final_url="https://example.com/",
        scan_status=ScanStatus.COMPLETED,
        main_document_status=200,
        elapsed_ms=100,
    )
    event = TraceEvent(
        timestamp="2026-08-19T12:00:00Z",
        node="scan_seed",
        event_type="scan",
        status="completed",
        elapsed_ms=100,
    )
    return {
        "run_id": "run-test-123",
        "seed_url": "https://example.com/",
        "seed_result": PageScanResult(observation=obs, findings=[finding]),
        "findings": [finding],
        "incidents": [incident],
        "analysis_status": AnalysisStatus.COMPLETED,
        "related_results": [],
        "operational_errors": [],
        "trace_events": [event],
    }


def test_renderer_writes_three_artifacts_and_escapes_untrusted_text(
    tmp_path: Path,
    report_state: AuditState,
) -> None:
    report_state["findings"][0] = report_state["findings"][0].model_copy(
        update={"message": "<script>alert(1)</script>"}
    )
    paths = ReportRenderer(tmp_path).write(report_state)

    assert paths.report_json.endswith("report.json")
    assert paths.report_html.endswith("report.html")
    assert paths.trace_json.endswith("trace.json")
    html = (tmp_path / report_state["run_id"] / "report.html").read_text("utf-8")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_report_and_trace_are_valid_json(
    tmp_path: Path,
    report_state: AuditState,
) -> None:
    paths = ReportRenderer(tmp_path).write(report_state)

    report = json.loads(Path(paths.report_json).read_text("utf-8"))
    trace = json.loads(Path(paths.trace_json).read_text("utf-8"))

    assert report["run_id"] == report_state["run_id"]
    assert isinstance(trace, list)
