from collections.abc import Sequence
from typing import Any

from pydantic import Field

from website_reliability_agent.models import RunReport, StrictModel


class EvalCase(StrictModel):
    name: str
    path: str
    expects_agent_path: bool
    expected_seed_codes: list[str]
    expected_repeated_signatures: list[str]


class CaseResult(StrictModel):
    name: str
    routing_correct: bool
    expected_fingerprints: int = Field(default=0, ge=0)
    detected_fingerprints: int = Field(default=0, ge=0)
    valid_claim_references: int = Field(default=0, ge=0)
    all_claim_references: int = Field(default=0, ge=0)
    expected_patterns: int = Field(default=0, ge=0)
    detected_patterns: int = Field(default=0, ge=0)
    related_pages_scanned: int = Field(default=0, ge=0)
    schema_valid: bool = True


class EvalMetrics(StrictModel):
    routing_accuracy: float
    finding_recall: float
    grounded_claim_precision: float
    pattern_recall: float
    related_page_limit: int
    schema_validity: float


def _ratio(numerator: int, denominator: int) -> float:
    return 1.0 if denominator == 0 else numerator / denominator


def evaluate_case(
    case_def: dict[str, Any],
    report: dict[str, Any],
    trace: list[dict[str, Any]],
) -> CaseResult:
    """Evaluate a single test case execution against expected ground truth."""
    name = case_def["name"]
    expects_agent = case_def["expects_agent_path"]
    expected_seed_codes = set(case_def.get("expected_seed_codes", []))
    expected_patterns = set(case_def.get("expected_repeated_signatures", []))

    # Schema validity check
    schema_valid = True
    try:
        RunReport.model_validate(report)
    except Exception:
        schema_valid = False

    # Routing accuracy check
    analysis_status = report.get("analysis_status")
    if expects_agent:
        routing_correct = analysis_status in {"completed", "review_rejected"}
    else:
        routing_correct = analysis_status == "not_needed"

    # Finding recall on seed page
    detected_codes = {f["check_code"] for f in report.get("findings", [])}
    detected_fingerprints = len(expected_seed_codes.intersection(detected_codes))

    # Grounded claim precision
    all_finding_ids = {f["finding_id"] for f in report.get("findings", [])}
    all_claim_refs = 0
    valid_claim_refs = 0
    incident_summaries: list[str] = []

    for inc in report.get("incidents", []):
        for fid in inc.get("finding_ids", []):
            all_claim_refs += 1
            if fid in all_finding_ids:
                valid_claim_refs += 1
        incident_summaries.append(inc.get("summary", "") + " " + inc.get("title", ""))

    # Pattern recall
    detected_patterns = 0
    for pattern in expected_patterns:
        if any(pattern.lower() in text.lower() for text in incident_summaries):
            detected_patterns += 1

    pages_scanned = report.get("pages_scanned", 1)
    related_scanned = max(0, pages_scanned - 1)

    return CaseResult(
        name=name,
        routing_correct=routing_correct,
        expected_fingerprints=len(expected_seed_codes),
        detected_fingerprints=detected_fingerprints,
        valid_claim_references=valid_claim_refs,
        all_claim_references=all_claim_refs,
        expected_patterns=len(expected_patterns),
        detected_patterns=detected_patterns,
        related_pages_scanned=related_scanned,
        schema_valid=schema_valid,
    )


def calculate_metrics(results: Sequence[CaseResult]) -> EvalMetrics:
    """Calculate aggregate evaluation metrics across all test case results."""
    expected_fingerprints = sum(item.expected_fingerprints for item in results)
    expected_patterns = sum(item.expected_patterns for item in results)
    references = sum(item.all_claim_references for item in results)

    return EvalMetrics(
        routing_accuracy=_ratio(
            sum(1 for item in results if item.routing_correct),
            len(results),
        ),
        finding_recall=_ratio(
            sum(item.detected_fingerprints for item in results),
            expected_fingerprints,
        ),
        grounded_claim_precision=_ratio(
            sum(item.valid_claim_references for item in results),
            references,
        ),
        pattern_recall=_ratio(
            sum(item.detected_patterns for item in results),
            expected_patterns,
        ),
        related_page_limit=max(
            (item.related_pages_scanned for item in results),
            default=0,
        ),
        schema_validity=_ratio(
            sum(1 for item in results if item.schema_valid),
            len(results),
        ),
    )
