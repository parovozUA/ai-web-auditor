from dataclasses import dataclass
from time import perf_counter
from typing import Any, Protocol, TypedDict

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from website_reliability_agent.agents import AgentBackend
from website_reliability_agent.grounding import validate_grounded_incidents
from website_reliability_agent.models import (
    AnalysisStatus,
    ArtifactPaths,
    Finding,
    FindingCategory,
    FindingCode,
    Incident,
    InvestigationResult,
    PageObservation,
    PageScanResult,
    RawObservation,
    ReviewDecision,
    ReviewResult,
    ScanStatus,
    TraceEvent,
)
from website_reliability_agent.tracing import TraceRecorder
from website_reliability_agent.urls import (
    Origin,
    canonicalize_url,
    origin_of,
    sanitize_url,
    select_related_urls,
)


class AuditState(TypedDict, total=False):
    run_id: str
    seed_url: str
    seed_origin: Origin
    seed_result: PageScanResult
    related_urls: list[str]
    related_results: list[PageScanResult]
    findings: list[Finding]
    tool_call_count: int
    investigation: InvestigationResult | None
    review: ReviewResult | None
    incidents: list[Incident]
    analysis_status: AnalysisStatus
    operational_errors: list[str]
    trace_events: list[TraceEvent]
    artifact_paths: ArtifactPaths | None


class Scanner(Protocol):
    async def scan(
        self,
        url: str,
        *,
        enabled_codes: set[FindingCode] | None = None,
        expected_origin: Origin | None = None,
    ) -> PageScanResult:
        raise NotImplementedError


class ArtifactWriter(Protocol):
    def write(self, state: AuditState) -> ArtifactPaths:
        raise NotImplementedError


@dataclass(frozen=True)
class WorkflowServices:
    scanner: Scanner
    agents: AgentBackend
    writer: ArtifactWriter
    trace: TraceRecorder


def initial_state(run_id: str, seed_url: str) -> AuditState:
    return {
        "run_id": run_id,
        "seed_url": seed_url,
        "findings": [],
        "related_urls": [],
        "related_results": [],
        "tool_call_count": 0,
        "incidents": [],
        "analysis_status": AnalysisStatus.NOT_NEEDED,
        "operational_errors": [],
        "trace_events": [],
    }


def exit_code_for(state: AuditState) -> int:
    seed_res = state.get("seed_result")
    if seed_res is not None and seed_res.observation.scan_status is ScanStatus.FAILED:
        return 2
    if state.get("findings"):
        return 1
    return 0


def _scan_seed_node(services: WorkflowServices) -> Any:
    async def _node(state: AuditState) -> dict[str, Any]:
        started = perf_counter()
        url = state["seed_url"]
        try:
            result = await services.scanner.scan(url)
            seed_origin = origin_of(result.observation.final_url)
            related = select_related_urls(
                result.observation.internal_links,
                base_url=result.observation.final_url,
                seed_url=result.observation.final_url,
                limit=5,
            )
            elapsed = round((perf_counter() - started) * 1_000)
            status_str = (
                "completed"
                if result.observation.scan_status is ScanStatus.COMPLETED
                else "failed"
            )
            services.trace.record(
                node="scan_seed",
                event_type="scan",
                status=status_str,
                elapsed_ms=elapsed,
                counts={
                    "findings": len(result.findings),
                    "internal_links": len(result.observation.internal_links),
                },
            )
            return {
                "seed_result": result,
                "seed_origin": seed_origin,
                "related_urls": related,
                "findings": result.findings,
            }
        except Exception as exc:
            elapsed = round((perf_counter() - started) * 1_000)
            services.trace.record(
                node="scan_seed",
                event_type="scan",
                status="failed",
                elapsed_ms=elapsed,
                error=exc,
            )
            try:
                canonical = canonicalize_url(url)
            except Exception:
                canonical = url
            syn_obs = RawObservation(
                check_code=FindingCode.NAVIGATION_FAILED,
                page_url=canonical,
                message=f"Seed navigation failed ({type(exc).__name__})",
                signature=f"navigation_failed:{type(exc).__name__.lower()}",
                evidence=canonical,
            )
            syn_finding = Finding(
                finding_id="finding-seed-nav-fail",
                check_code=FindingCode.NAVIGATION_FAILED,
                category=FindingCategory.NAVIGATION,
                source_page=sanitize_url(canonical),
                signature=f"navigation_failed:{type(exc).__name__.lower()}",
                message=f"Seed navigation failed ({type(exc).__name__})",
            )
            syn_result = PageScanResult(
                observation=PageObservation(
                    requested_url=sanitize_url(canonical),
                    final_url=sanitize_url(canonical),
                    scan_status=ScanStatus.FAILED,
                    elapsed_ms=elapsed,
                    operational_error=type(exc).__name__,
                    raw_observations=[syn_obs],
                ),
                findings=[syn_finding],
            )
            try:
                origin = origin_of(canonical)
            except Exception:
                origin = ("https", "unknown", 443)
            return {
                "seed_result": syn_result,
                "seed_origin": origin,
                "related_urls": [],
                "findings": [syn_finding],
                "operational_errors": [type(exc).__name__],
            }

    return _node


def _route_after_seed(state: AuditState) -> str:
    seed_res = state.get("seed_result")
    if seed_res is None or seed_res.observation.scan_status is ScanStatus.FAILED:
        return "clean_or_failed"
    if not state.get("findings"):
        return "clean_or_failed"
    return "investigate"


def _tool_request_node(services: WorkflowServices) -> Any:
    async def _node(state: AuditState) -> dict[str, Any]:
        started = perf_counter()
        findings = state.get("findings", [])
        try:
            should_scan = await services.agents.request_related_scan(findings)
            elapsed = round((perf_counter() - started) * 1_000)
            if not should_scan:
                services.trace.record(
                    node="investigator_tool_request",
                    event_type="agent_tool_decision",
                    status="rejected",
                    elapsed_ms=elapsed,
                    model_usage=services.agents.last_usage,
                )
                return {
                    "analysis_status": AnalysisStatus.AGENT_ANALYSIS_UNAVAILABLE,
                    "operational_errors": state.get("operational_errors", [])
                    + ["Invalid tool call decision"],
                }
            services.trace.record(
                node="investigator_tool_request",
                event_type="agent_tool_decision",
                status="completed",
                elapsed_ms=elapsed,
                tool_name="scan_related_pages",
                model_usage=services.agents.last_usage,
            )
            return {}
        except Exception as exc:
            elapsed = round((perf_counter() - started) * 1_000)
            services.trace.record(
                node="investigator_tool_request",
                event_type="agent_tool_decision",
                status="failed",
                elapsed_ms=elapsed,
                error=exc,
            )
            return {
                "analysis_status": AnalysisStatus.AGENT_ANALYSIS_UNAVAILABLE,
                "operational_errors": state.get("operational_errors", [])
                + [type(exc).__name__],
            }

    return _node


def _route_after_tool_request(state: AuditState) -> str:
    if state.get("analysis_status") is AnalysisStatus.AGENT_ANALYSIS_UNAVAILABLE:
        return "render"
    return "scan"


def _related_scan_node(services: WorkflowServices) -> Any:
    async def _node(state: AuditState) -> dict[str, Any]:
        started = perf_counter()
        if state.get("tool_call_count", 0) >= 1:
            raise RuntimeError("Related page scan tool can only be invoked once")

        related_urls = state.get("related_urls", [])
        seed_codes = {f.check_code for f in state.get("findings", [])}
        seed_origin = state.get("seed_origin")

        results: list[PageScanResult] = []
        all_findings = list(state.get("findings", []))

        for url in related_urls:
            res = await services.scanner.scan(
                url,
                enabled_codes=seed_codes,
                expected_origin=seed_origin,
            )
            results.append(res)
            all_findings.extend(res.findings)

        elapsed = round((perf_counter() - started) * 1_000)
        services.trace.record(
            node="scan_related_pages",
            event_type="tool_execution",
            status="completed",
            elapsed_ms=elapsed,
            tool_name="scan_related_pages",
            counts={
                "pages_scanned": len(results),
                "findings": len(all_findings),
            },
        )
        return {
            "tool_call_count": 1,
            "related_results": results,
            "findings": all_findings,
        }

    return _node


def _synthesis_node(services: WorkflowServices) -> Any:
    async def _node(state: AuditState) -> dict[str, Any]:
        started = perf_counter()
        seed_res = state.get("seed_result")
        seed_findings = seed_res.findings if seed_res else []
        related_findings: list[Finding] = []
        for r in state.get("related_results", []):
            related_findings.extend(r.findings)

        try:
            investigation = await services.agents.investigate(seed_findings, related_findings)
            elapsed = round((perf_counter() - started) * 1_000)
            services.trace.record(
                node="investigator_synthesis",
                event_type="agent_synthesis",
                status="completed",
                elapsed_ms=elapsed,
                counts={"incidents": len(investigation.incidents)},
                model_usage=services.agents.last_usage,
            )
            return {
                "investigation": investigation,
            }
        except Exception as exc:
            elapsed = round((perf_counter() - started) * 1_000)
            services.trace.record(
                node="investigator_synthesis",
                event_type="agent_synthesis",
                status="failed",
                elapsed_ms=elapsed,
                error=exc,
            )
            return {
                "analysis_status": AnalysisStatus.AGENT_ANALYSIS_UNAVAILABLE,
                "operational_errors": state.get("operational_errors", [])
                + [type(exc).__name__],
            }

    return _node


def _route_after_synthesis(state: AuditState) -> str:
    if state.get("analysis_status") is AnalysisStatus.AGENT_ANALYSIS_UNAVAILABLE:
        return "render"
    return "review"


def _review_node(services: WorkflowServices) -> Any:
    async def _node(state: AuditState) -> dict[str, Any]:
        started = perf_counter()
        investigation = state.get("investigation")
        if investigation is None:
            return {
                "analysis_status": AnalysisStatus.REVIEW_UNAVAILABLE,
                "operational_errors": state.get("operational_errors", [])
                + ["Missing investigation result"],
            }
        findings = state.get("findings", [])
        try:
            review = await services.agents.review(investigation, findings)
            elapsed = round((perf_counter() - started) * 1_000)
            services.trace.record(
                node="reviewer_agent",
                event_type="agent_review",
                status="completed",
                elapsed_ms=elapsed,
                counts={"incidents": len(review.incidents)},
                model_usage=services.agents.last_usage,
            )
            return {
                "review": review,
            }
        except Exception as exc:
            elapsed = round((perf_counter() - started) * 1_000)
            services.trace.record(
                node="reviewer_agent",
                event_type="agent_review",
                status="failed",
                elapsed_ms=elapsed,
                error=exc,
            )
            return {
                "analysis_status": AnalysisStatus.REVIEW_UNAVAILABLE,
                "operational_errors": state.get("operational_errors", [])
                + [type(exc).__name__],
            }

    return _node


def _route_after_review(state: AuditState) -> str:
    if state.get("analysis_status") is AnalysisStatus.REVIEW_UNAVAILABLE:
        return "render"
    return "validate"


def _validation_node(services: WorkflowServices) -> Any:
    def _node(state: AuditState) -> dict[str, Any]:
        started = perf_counter()
        review = state.get("review")
        if review is None:
            return {
                "analysis_status": AnalysisStatus.REVIEW_UNAVAILABLE,
            }
        if review.decision is ReviewDecision.REJECT:
            elapsed = round((perf_counter() - started) * 1_000)
            services.trace.record(
                node="validate_review",
                event_type="validation",
                status="rejected",
                elapsed_ms=elapsed,
                counts={"incidents": 0},
            )
            return {
                "incidents": [],
                "analysis_status": AnalysisStatus.REVIEW_REJECTED,
            }
        incidents = validate_grounded_incidents(review, state.get("findings", []))
        elapsed = round((perf_counter() - started) * 1_000)
        services.trace.record(
            node="validate_review",
            event_type="validation",
            status="completed",
            elapsed_ms=elapsed,
            counts={"incidents": len(incidents)},
        )
        return {
            "incidents": incidents,
            "analysis_status": AnalysisStatus.COMPLETED,
        }

    return _node


def _render_node(services: WorkflowServices) -> Any:
    def _node(state: AuditState) -> dict[str, Any]:
        started = perf_counter()
        state["trace_events"] = list(services.trace.events)
        paths = services.writer.write(state)
        elapsed = round((perf_counter() - started) * 1_000)
        services.trace.record(
            node="render_report",
            event_type="reporting",
            status="completed",
            elapsed_ms=elapsed,
        )
        return {
            "artifact_paths": paths,
            "trace_events": list(services.trace.events),
        }

    return _node


def build_audit_graph(services: WorkflowServices) -> CompiledStateGraph[Any, Any, Any, Any]:
    """Build the bounded LangGraph state machine with deterministic routing."""
    graph: StateGraph[AuditState] = StateGraph(AuditState)

    graph.add_node("scan_seed", _scan_seed_node(services))
    graph.add_node("investigator_tool_request", _tool_request_node(services))
    graph.add_node("scan_related_pages", _related_scan_node(services))
    graph.add_node("investigator_synthesis", _synthesis_node(services))
    graph.add_node("reviewer_agent", _review_node(services))
    graph.add_node("validate_review", _validation_node(services))
    graph.add_node("render_report", _render_node(services))

    graph.set_entry_point("scan_seed")

    graph.add_conditional_edges(
        "scan_seed",
        _route_after_seed,
        {
            "clean_or_failed": "render_report",
            "investigate": "investigator_tool_request",
        },
    )
    graph.add_conditional_edges(
        "investigator_tool_request",
        _route_after_tool_request,
        {
            "scan": "scan_related_pages",
            "render": "render_report",
        },
    )
    graph.add_edge("scan_related_pages", "investigator_synthesis")
    graph.add_conditional_edges(
        "investigator_synthesis",
        _route_after_synthesis,
        {
            "review": "reviewer_agent",
            "render": "render_report",
        },
    )
    graph.add_conditional_edges(
        "reviewer_agent",
        _route_after_review,
        {
            "validate": "validate_review",
            "render": "render_report",
        },
    )
    graph.add_edge("validate_review", "render_report")
    graph.add_edge("render_report", END)

    return graph.compile()
