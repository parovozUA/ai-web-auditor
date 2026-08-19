import hashlib
from collections.abc import Sequence

from ai_web_auditor.models import Finding, Incident, ReviewResult


def _hash_incident(
    title: str,
    finding_ids: Sequence[str],
    affected_pages: Sequence[str],
) -> str:
    f_ids = ",".join(sorted(finding_ids))
    pages = ",".join(sorted(affected_pages))
    raw = f"{title.strip().lower()}|{f_ids}|{pages}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    return f"incident-{digest}"


def validate_grounded_incidents(
    review: ReviewResult,
    known_findings: Sequence[Finding],
) -> list[Incident]:
    """Pure, deterministic validation layer pruning ungrounded claims and deriving pages."""
    finding_map = {f.finding_id: f for f in known_findings}
    valid_incidents: list[Incident] = []

    for proposal in review.incidents:
        valid_ids = [fid for fid in proposal.finding_ids if fid in finding_map]
        if not valid_ids:
            continue

        pages = sorted({finding_map[fid].source_page for fid in valid_ids})
        incident_id = _hash_incident(proposal.title, valid_ids, pages)

        valid_incidents.append(
            Incident(
                incident_id=incident_id,
                title=proposal.title,
                summary=proposal.summary,
                severity=proposal.severity,
                finding_ids=sorted(valid_ids),
                affected_pages=pages,
                remediation=proposal.remediation,
            )
        )

    return valid_incidents
