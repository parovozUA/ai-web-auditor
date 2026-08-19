from typing import cast

import pytest

from tests.fakes import FakeAgentBackend, FakeArtifactWriter, FakeScanner
from website_reliability_agent.models import (
    AnalysisStatus,
    Finding,
    FindingCategory,
    FindingCode,
    IncidentProposal,
    InvestigationResult,
    PageObservation,
    PageScanResult,
    RawObservation,
    ReviewDecision,
    ReviewResult,
    ScanStatus,
    Severity,
)
from website_reliability_agent.tracing import TraceRecorder
from website_reliability_agent.workflow import (
    AuditState,
    WorkflowServices,
    build_audit_graph,
    exit_code_for,
    initial_state,
)


@pytest.fixture
def clean_result() -> PageScanResult:
    return PageScanResult(
        observation=PageObservation(
            requested_url="https://example.com/",
            final_url="https://example.com/",
            scan_status=ScanStatus.COMPLETED,
            main_document_status=200,
            elapsed_ms=100,
            internal_links=[],
            raw_observations=[],
        ),
        findings=[],
    )


@pytest.fixture
def seed_result() -> PageScanResult:
    obs = RawObservation(
        check_code=FindingCode.TITLE_MISSING,
        page_url="https://example.com/",
        message="Missing title",
        signature="title_missing",
    )
    return PageScanResult(
        observation=PageObservation(
            requested_url="https://example.com/",
            final_url="https://example.com/",
            scan_status=ScanStatus.COMPLETED,
            main_document_status=200,
            elapsed_ms=120,
            internal_links=["https://example.com/related-1"],
            raw_observations=[obs],
        ),
        findings=[
            Finding(
                finding_id="finding-seed-1",
                check_code=FindingCode.TITLE_MISSING,
                category=FindingCategory.SEO,
                source_page="https://example.com/",
                signature="title_missing",
                message="Missing title",
            )
        ],
    )


@pytest.fixture
def related_results() -> dict[str, PageScanResult]:
    obs = RawObservation(
        check_code=FindingCode.TITLE_MISSING,
        page_url="https://example.com/related-1",
        message="Missing title",
        signature="title_missing",
    )
    return {
        "https://example.com/related-1": PageScanResult(
            observation=PageObservation(
                requested_url="https://example.com/related-1",
                final_url="https://example.com/related-1",
                scan_status=ScanStatus.COMPLETED,
                main_document_status=200,
                elapsed_ms=80,
                internal_links=[],
                raw_observations=[obs],
            ),
            findings=[
                Finding(
                    finding_id="finding-rel-1",
                    check_code=FindingCode.TITLE_MISSING,
                    category=FindingCategory.SEO,
                    source_page="https://example.com/related-1",
                    signature="title_missing",
                    message="Missing title",
                )
            ],
        )
    }


@pytest.fixture
def failed_seed_result() -> PageScanResult:
    return PageScanResult(
        observation=PageObservation(
            requested_url="https://example.com/",
            final_url="https://example.com/",
            scan_status=ScanStatus.FAILED,
            main_document_status=None,
            elapsed_ms=50,
            internal_links=[],
            raw_observations=[],
            operational_error="NavigationError",
        ),
        findings=[
            Finding(
                finding_id="finding-nav-fail",
                check_code=FindingCode.NAVIGATION_FAILED,
                category=FindingCategory.NAVIGATION,
                source_page="https://example.com/",
                signature="navigation_failed:navigationerror",
                message="Page navigation failed",
            )
        ],
    )


@pytest.fixture
def investigation() -> InvestigationResult:
    return InvestigationResult(
        summary="Found repeated missing titles.",
        incidents=[
            IncidentProposal(
                title="Missing titles",
                summary="Title missing on seed and related.",
                severity=Severity.LOW,
                finding_ids=["finding-seed-1", "finding-rel-1"],
                remediation="Add titles",
            )
        ],
    )


@pytest.fixture
def accepted_review(investigation: InvestigationResult) -> ReviewResult:
    return ReviewResult(
        decision=ReviewDecision.ACCEPT,
        incidents=investigation.incidents,
        notes="Grounded and valid.",
    )


async def test_clean_seed_skips_all_agent_calls(clean_result: PageScanResult) -> None:
    scanner = FakeScanner({"https://example.com/": clean_result})
    agents = FakeAgentBackend()
    writer = FakeArtifactWriter()
    graph = build_audit_graph(
        WorkflowServices(
            scanner=scanner,
            agents=agents,
            writer=writer,
            trace=TraceRecorder(),
        )
    )

    raw_state = await graph.ainvoke(initial_state("run-clean", "https://example.com/"))
    state = cast(AuditState, raw_state)

    assert agents.call_order == []
    assert state["tool_call_count"] == 0
    assert state["analysis_status"] is AnalysisStatus.NOT_NEEDED
    assert exit_code_for(state) == 0


async def test_findings_run_one_tool_scan_then_both_agents(
    seed_result: PageScanResult,
    related_results: dict[str, PageScanResult],
    investigation: InvestigationResult,
    accepted_review: ReviewResult,
) -> None:
    scanner = FakeScanner(
        {"https://example.com/": seed_result, **related_results}
    )
    agents = FakeAgentBackend(
        investigation=investigation,
        review=accepted_review,
    )
    graph = build_audit_graph(
        WorkflowServices(
            scanner=scanner,
            agents=agents,
            writer=FakeArtifactWriter(),
            trace=TraceRecorder(),
        )
    )

    raw_state = await graph.ainvoke(initial_state("run-agent", "https://example.com/"))
    state = cast(AuditState, raw_state)

    assert agents.call_order == ["tool_request", "investigate", "review"]
    assert state["tool_call_count"] == 1
    assert len(scanner.calls) <= 6
    assert state["analysis_status"] is AnalysisStatus.COMPLETED
    assert exit_code_for(state) == 1


async def test_investigator_failure_skips_reviewer_and_still_renders(
    seed_result: PageScanResult,
) -> None:
    scanner = FakeScanner({"https://example.com/": seed_result})
    agents = FakeAgentBackend(fail_on="tool_request")
    writer = FakeArtifactWriter()
    graph = build_audit_graph(
        WorkflowServices(
            scanner=scanner,
            agents=agents,
            writer=writer,
            trace=TraceRecorder(),
        )
    )

    raw_state = await graph.ainvoke(initial_state("run-fallback", "https://example.com/"))
    state = cast(AuditState, raw_state)

    assert agents.call_order == ["tool_request"]
    assert state["analysis_status"] is AnalysisStatus.AGENT_ANALYSIS_UNAVAILABLE
    assert state["incidents"] == []
    assert writer.received_state is not None


async def test_seed_navigation_failure_uses_exit_code_two(
    failed_seed_result: PageScanResult,
) -> None:
    scanner = FakeScanner({"https://example.com/": failed_seed_result})
    agents = FakeAgentBackend()
    graph = build_audit_graph(
        WorkflowServices(
            scanner=scanner,
            agents=agents,
            writer=FakeArtifactWriter(),
            trace=TraceRecorder(),
        )
    )

    raw_state = await graph.ainvoke(initial_state("run-failed", "https://example.com/"))
    state = cast(AuditState, raw_state)

    assert agents.call_order == []
    assert exit_code_for(state) == 2
