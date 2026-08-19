from typing import Any

from ai_web_auditor.agents import AgentUnavailableError
from ai_web_auditor.models import (
    ArtifactPaths,
    Finding,
    FindingCode,
    InvestigationResult,
    PageScanResult,
    ReviewResult,
)
from ai_web_auditor.urls import Origin
from ai_web_auditor.workflow import AuditState


class FakeScanner:
    def __init__(self, results_by_url: dict[str, PageScanResult]) -> None:
        self.results_by_url = results_by_url
        self.calls: list[tuple[str, set[FindingCode] | None, Origin | None]] = []

    async def scan(
        self,
        url: str,
        *,
        enabled_codes: set[FindingCode] | None = None,
        expected_origin: Origin | None = None,
    ) -> PageScanResult:
        self.calls.append((url, enabled_codes, expected_origin))
        if url not in self.results_by_url:
            raise KeyError(f"FakeScanner URL not found: {url}")
        return self.results_by_url[url]


class FakeAgentBackend:
    def __init__(
        self,
        *,
        investigation: InvestigationResult | None = None,
        review: ReviewResult | None = None,
        request_tool: bool = True,
        fail_on: str | None = None,
    ) -> None:
        self.investigation = investigation
        self.review_result = review
        self.request_tool = request_tool
        self.fail_on = fail_on
        self.call_order: list[str] = []
        self.last_usage: dict[str, int] = {}

    async def request_related_scan(self, seed_findings: list[Finding]) -> bool:
        self.call_order.append("tool_request")
        if self.fail_on == "tool_request":
            raise AgentUnavailableError("Injected failure during tool request")
        return self.request_tool

    async def investigate(
        self,
        seed_findings: list[Finding],
        related_findings: list[Finding],
    ) -> InvestigationResult:
        self.call_order.append("investigate")
        if self.fail_on == "investigate":
            raise AgentUnavailableError("Injected failure during investigation")
        if self.investigation is not None:
            return self.investigation
        return InvestigationResult(
            summary="Default fake investigation summary",
            incidents=[],
        )

    async def review(
        self,
        investigation: InvestigationResult,
        findings: list[Finding],
    ) -> ReviewResult:
        self.call_order.append("review")
        if self.fail_on == "review":
            raise AgentUnavailableError("Injected failure during review")
        if self.review_result is not None:
            return self.review_result
        return ReviewResult(
            decision="accept",  # type: ignore[arg-type]
            incidents=investigation.incidents,
            notes="Default fake review notes",
        )


class FakeArtifactWriter:
    def __init__(self, paths: ArtifactPaths | None = None) -> None:
        self.received_state: AuditState | None = None
        self.paths = paths or ArtifactPaths(
            directory="/tmp/fake-artifacts",
            report_json="/tmp/fake-artifacts/report.json",
            report_html="/tmp/fake-artifacts/report.html",
            trace_json="/tmp/fake-artifacts/trace.json",
        )

    def write(self, state: AuditState) -> ArtifactPaths:
        self.received_state = state
        return self.paths


class RepeatingAgentBackend(FakeAgentBackend):
    """Deterministic agent double that groups findings appearing on >=2 distinct pages."""

    async def investigate(
        self,
        seed_findings: list[Finding],
        related_findings: list[Finding],
    ) -> InvestigationResult:
        self.call_order.append("investigate")
        all_findings = seed_findings + related_findings
        signatures: dict[str, list[Finding]] = {}
        for finding in all_findings:
            signatures.setdefault(finding.signature, []).append(finding)

        incidents: list[Any] = []
        for signature, grouped in signatures.items():
            pages = {f.source_page for f in grouped}
            if len(pages) >= 2:
                from ai_web_auditor.models import IncidentProposal, Severity

                incidents.append(
                    IncidentProposal(
                        title=f"Recurring issue: {grouped[0].check_code.value}",
                        summary=f"Signature {signature} repeats on {len(pages)} pages.",
                        severity=Severity.MEDIUM,
                        finding_ids=[f.finding_id for f in grouped],
                        remediation="Apply consistent site-wide fix.",
                    )
                )

        return InvestigationResult(
            summary=f"Identified {len(incidents)} cross-page incident(s).",
            incidents=incidents,
        )

    async def review(
        self,
        investigation: InvestigationResult,
        findings: list[Finding],
    ) -> ReviewResult:
        self.call_order.append("review")
        from ai_web_auditor.models import ReviewDecision

        return ReviewResult(
            decision=ReviewDecision.ACCEPT,
            incidents=investigation.incidents,
            notes="Accepted all grounded cross-page incidents.",
        )
