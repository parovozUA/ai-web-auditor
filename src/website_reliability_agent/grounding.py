from hashlib import sha256

from website_reliability_agent.models import (
    Finding,
    Incident,
    ReviewDecision,
    ReviewResult,
)


def validate_grounded_incidents(
    review: ReviewResult,
    findings: list[Finding],
) -> list[Incident]:
    """Validate incident proposals against raw findings, deriving affected pages and IDs."""
    if review.decision is ReviewDecision.REJECT:
        return []

    by_id = {item.finding_id: item for item in findings}
    validated: list[Incident] = []

    for proposal in review.incidents:
        retained = list(dict.fromkeys(
            identifier
            for identifier in proposal.finding_ids
            if identifier in by_id
        ))
        if not retained:
            continue

        pages = sorted({by_id[identifier].source_page for identifier in retained})
        digest = sha256("|".join(sorted(retained)).encode("utf-8")).hexdigest()[:16]

        validated.append(
            Incident(
                incident_id=f"incident-{digest}",
                title=proposal.title,
                summary=proposal.summary,
                severity=proposal.severity,
                finding_ids=retained,
                affected_pages=pages,
                remediation=proposal.remediation,
            )
        )

    return validated
