from website_reliability_agent.grounding import validate_grounded_incidents
from website_reliability_agent.models import (
    Finding,
    FindingCategory,
    FindingCode,
    IncidentProposal,
    ReviewDecision,
    ReviewResult,
    Severity,
)


def finding(identifier: str, page: str) -> Finding:
    return Finding(
        finding_id=identifier,
        check_code=FindingCode.TITLE_MISSING,
        category=FindingCategory.SEO,
        source_page=page,
        signature="title_missing",
        message="Page title is missing.",
    )


def test_validator_removes_unknown_ids_and_derives_pages() -> None:
    review = ReviewResult(
        decision=ReviewDecision.CORRECT,
        incidents=[
            IncidentProposal(
                title="Repeated missing title",
                summary="Titles are missing.",
                severity=Severity.MEDIUM,
                finding_ids=["known-a", "unknown", "known-b"],
                remediation="Add distinct title elements.",
            )
        ],
        notes="Removed unsupported evidence.",
    )

    incidents = validate_grounded_incidents(
        review,
        [
            finding("known-a", "https://example.com/a"),
            finding("known-b", "https://example.com/b"),
        ],
    )

    assert incidents[0].finding_ids == ["known-a", "known-b"]
    assert incidents[0].affected_pages == [
        "https://example.com/a",
        "https://example.com/b",
    ]


def test_validator_drops_incident_with_no_known_evidence() -> None:
    review = ReviewResult(
        decision=ReviewDecision.ACCEPT,
        incidents=[
            IncidentProposal(
                title="Invented",
                summary="Unsupported.",
                severity=Severity.HIGH,
                finding_ids=["unknown"],
                remediation="None.",
            )
        ],
        notes="",
    )

    assert validate_grounded_incidents(review, []) == []


def test_rejected_review_produces_no_agent_incidents() -> None:
    review = ReviewResult(
        decision=ReviewDecision.REJECT,
        incidents=[],
        notes="The grouping is unsupported.",
    )

    assert validate_grounded_incidents(review, [finding("known", "https://example.com")]) == []
