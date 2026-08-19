from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FindingCode(StrEnum):
    TITLE_MISSING = "title_missing"
    H1_MISSING = "h1_missing"
    H1_MULTIPLE = "h1_multiple"
    META_DESCRIPTION_MISSING = "meta_description_missing"
    PAGE_ERROR = "page_error"
    CONSOLE_ERROR = "console_error"
    BROKEN_INTERNAL_LINK = "broken_internal_link"
    REQUEST_FAILED = "request_failed"
    RESOURCE_HTTP_ERROR = "resource_http_error"
    NAVIGATION_FAILED = "navigation_failed"
    NAVIGATION_HTTP_ERROR = "navigation_http_error"


class FindingCategory(StrEnum):
    SEO = "seo"
    JAVASCRIPT = "javascript"
    LINKS = "links"
    RESOURCES = "resources"
    NAVIGATION = "navigation"


class ScanStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class AnalysisStatus(StrEnum):
    NOT_NEEDED = "not_needed"
    COMPLETED = "completed"
    AGENT_ANALYSIS_UNAVAILABLE = "agent_analysis_unavailable"
    REVIEW_UNAVAILABLE = "review_unavailable"
    REVIEW_REJECTED = "review_rejected"


class ReviewDecision(StrEnum):
    ACCEPT = "accept"
    CORRECT = "correct"
    REJECT = "reject"


class RawObservation(StrictModel):
    check_code: FindingCode
    page_url: str
    target_url: str | None = None
    message: str
    signature: str
    evidence: str | None = None


class PageObservation(StrictModel):
    requested_url: str
    final_url: str
    scan_status: ScanStatus
    main_document_status: int | None = None
    elapsed_ms: int = Field(ge=0)
    internal_links: list[str] = Field(default_factory=list)
    raw_observations: list[RawObservation] = Field(default_factory=list)
    operational_error: str | None = None


class Finding(StrictModel):
    finding_id: str = Field(min_length=1)
    check_code: FindingCode
    category: FindingCategory
    source_page: str
    target_url: str | None = None
    signature: str
    message: str
    evidence: str | None = None
    occurrence_count: int = Field(default=1, ge=1)


class PageScanResult(StrictModel):
    observation: PageObservation
    findings: list[Finding] = Field(default_factory=list)


class IncidentProposal(StrictModel):
    title: str = Field(min_length=1, max_length=160)
    summary: str = Field(min_length=1, max_length=800)
    severity: Severity
    finding_ids: list[str] = Field(min_length=1)
    remediation: str = Field(min_length=1, max_length=800)


class InvestigationResult(StrictModel):
    summary: str = Field(min_length=1, max_length=800)
    incidents: list[IncidentProposal] = Field(default_factory=list)


class ReviewResult(StrictModel):
    decision: ReviewDecision
    incidents: list[IncidentProposal] = Field(default_factory=list)
    notes: str = Field(max_length=800)


class Incident(StrictModel):
    incident_id: str
    title: str
    summary: str
    severity: Severity
    finding_ids: list[str] = Field(min_length=1)
    affected_pages: list[str] = Field(min_length=1)
    remediation: str


class TraceEvent(StrictModel):
    timestamp: str
    node: str
    event_type: str
    status: str
    elapsed_ms: int = Field(ge=0)
    tool_name: str | None = None
    arguments: dict[str, str] = Field(default_factory=dict)
    counts: dict[str, int] = Field(default_factory=dict)
    model_usage: dict[str, int] = Field(default_factory=dict)
    error_category: str | None = None


class ArtifactPaths(StrictModel):
    directory: str
    report_json: str
    report_html: str
    trace_json: str


class RunReport(StrictModel):
    run_id: str
    seed_url: str
    completed_at: str
    exit_code: int
    analysis_status: AnalysisStatus
    pages_scanned: int = Field(ge=0, le=6)
    findings: list[Finding]
    incidents: list[Incident]
    operational_errors: list[str]
