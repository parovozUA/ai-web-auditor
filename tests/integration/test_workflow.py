import json
from pathlib import Path

import pytest

from ai_web_auditor.cli import ScanOptions, run_scan
from tests.fakes import FakeAgentBackend, RepeatingAgentBackend
from tests.fixture_site import FixtureSite

pytestmark = pytest.mark.integration


async def test_clean_workflow_writes_artifacts_without_agent_calls(
    fixture_site: FixtureSite,
    tmp_path: Path,
) -> None:
    agents = FakeAgentBackend()
    exit_code, paths = await run_scan(
        ScanOptions(
            url=fixture_site.url("/clean"),
            allow_private=True,
            artifact_root=tmp_path,
        ),
        agents=agents,
    )

    assert exit_code == 0
    assert agents.call_order == []
    assert Path(paths.report_json).exists()
    assert Path(paths.report_html).exists()
    assert Path(paths.trace_json).exists()


async def test_seo_workflow_scans_at_most_five_related_pages_and_reviews(
    fixture_site: FixtureSite,
    tmp_path: Path,
) -> None:
    repeating_agent_backend = RepeatingAgentBackend()
    exit_code, paths = await run_scan(
        ScanOptions(
            url=fixture_site.url("/seo"),
            allow_private=True,
            artifact_root=tmp_path,
        ),
        agents=repeating_agent_backend,
    )
    report = json.loads(Path(paths.report_json).read_text("utf-8"))
    trace = json.loads(Path(paths.trace_json).read_text("utf-8"))

    assert exit_code == 1
    assert report["pages_scanned"] <= 6
    assert repeating_agent_backend.call_order == [
        "tool_request",
        "investigate",
        "review",
    ]
    assert sum(event.get("tool_name") == "scan_related_pages" for event in trace) == 1


async def test_investigator_failure_still_renders_deterministic_artifacts(
    fixture_site: FixtureSite,
    tmp_path: Path,
) -> None:
    failing_backend = FakeAgentBackend(fail_on="tool_request")
    exit_code, paths = await run_scan(
        ScanOptions(
            url=fixture_site.url("/seo"),
            allow_private=True,
            artifact_root=tmp_path,
        ),
        agents=failing_backend,
    )
    report = json.loads(Path(paths.report_json).read_text("utf-8"))

    assert exit_code == 1
    assert report["analysis_status"] == "agent_analysis_unavailable"
    assert len(report["findings"]) > 0
    assert "review" not in failing_backend.call_order
    assert Path(paths.report_json).exists()
    assert Path(paths.report_html).exists()
    assert Path(paths.trace_json).exists()
