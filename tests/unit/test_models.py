import pytest
from pydantic import ValidationError

from ai_web_auditor.models import (
    Finding,
    FindingCategory,
    FindingCode,
    IncidentProposal,
    Severity,
)


def test_finding_serializes_enum_values() -> None:
    finding = Finding(
        finding_id="finding-123",
        check_code=FindingCode.TITLE_MISSING,
        category=FindingCategory.SEO,
        source_page="https://example.com/",
        signature="title_missing",
        message="The page title is missing.",
        occurrence_count=1,
    )

    assert finding.model_dump(mode="json")["check_code"] == "title_missing"


def test_incident_proposal_requires_at_least_one_finding_id() -> None:
    with pytest.raises(ValidationError):
        IncidentProposal(
            title="Repeated title issue",
            summary="The issue repeats.",
            severity=Severity.MEDIUM,
            finding_ids=[],
            remediation="Add a title.",
        )
