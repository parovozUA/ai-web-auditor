import re
from collections.abc import Iterable
from hashlib import sha256

from website_reliability_agent.models import (
    Finding,
    FindingCategory,
    FindingCode,
    RawObservation,
)
from website_reliability_agent.urls import sanitize_url

CATEGORY_BY_CODE: dict[FindingCode, FindingCategory] = {
    FindingCode.TITLE_MISSING: FindingCategory.SEO,
    FindingCode.H1_MISSING: FindingCategory.SEO,
    FindingCode.H1_MULTIPLE: FindingCategory.SEO,
    FindingCode.META_DESCRIPTION_MISSING: FindingCategory.SEO,
    FindingCode.PAGE_ERROR: FindingCategory.JAVASCRIPT,
    FindingCode.CONSOLE_ERROR: FindingCategory.JAVASCRIPT,
    FindingCode.BROKEN_INTERNAL_LINK: FindingCategory.LINKS,
    FindingCode.REQUEST_FAILED: FindingCategory.RESOURCES,
    FindingCode.RESOURCE_HTTP_ERROR: FindingCategory.RESOURCES,
    FindingCode.NAVIGATION_FAILED: FindingCategory.NAVIGATION,
    FindingCode.NAVIGATION_HTTP_ERROR: FindingCategory.NAVIGATION,
}

_HEX_DECIMAL_PATTERN = re.compile(
    r"(?:\b0x[0-9a-fA-F]+\b|\b[0-9a-fA-F]{8,}\b|\b\d+\b)"
)
_WHITESPACE_PATTERN = re.compile(r"\s+")
_URL_PATTERN = re.compile(r"https?://[^\s\"'>]+")


def normalize_message_signature(message: str) -> str:
    """Normalize whitespace, lowercase, and collapse dynamic numbers/hashes."""
    collapsed = _WHITESPACE_PATTERN.sub(" ", message).strip().lower()
    return _HEX_DECIMAL_PATTERN.sub("<n>", collapsed)


def _bounded(text: str, max_length: int) -> str:
    return text if len(text) <= max_length else text[:max_length]


def _sanitize_text(text: str) -> str:
    def _mask_match(match: re.Match[str]) -> str:
        url = match.group(0)
        try:
            return sanitize_url(url)
        except Exception:
            return url

    return _URL_PATTERN.sub(_mask_match, text)


def _seo_observation(code: FindingCode, page_url: str, message: str) -> RawObservation:
    return RawObservation(
        check_code=code,
        page_url=page_url,
        message=message,
        signature=code.value,
    )


def collect_seo_observations(
    *,
    page_url: str,
    title: str | None,
    h1_count: int,
    meta_description: str | None,
    enabled_codes: set[FindingCode] | None = None,
) -> list[RawObservation]:
    """Collect pure SEO check observations for title, H1s, and meta description."""
    candidates: list[RawObservation] = []
    if not title or not title.strip():
        candidates.append(
            _seo_observation(FindingCode.TITLE_MISSING, page_url, "Page title is missing.")
        )
    if h1_count == 0:
        candidates.append(
            _seo_observation(FindingCode.H1_MISSING, page_url, "Page H1 is missing.")
        )
    elif h1_count > 1:
        candidates.append(
            _seo_observation(
                FindingCode.H1_MULTIPLE,
                page_url,
                f"Page contains {h1_count} H1 elements.",
            )
        )
    if not meta_description or not meta_description.strip():
        candidates.append(
            _seo_observation(
                FindingCode.META_DESCRIPTION_MISSING,
                page_url,
                "Meta description is missing.",
            )
        )
    return [
        item for item in candidates
        if enabled_codes is None or item.check_code in enabled_codes
    ]


def normalize_findings(observations: Iterable[RawObservation]) -> list[Finding]:
    """Normalize, deduplicate, mask, and compute stable finding IDs."""
    grouped: dict[tuple[str, str, str, str], Finding] = {}
    for raw in observations:
        page = sanitize_url(raw.page_url)
        target = sanitize_url(raw.target_url) if raw.target_url else None
        signature = normalize_message_signature(raw.signature)
        key = (raw.check_code.value, page, target or "", signature)
        existing = grouped.get(key)
        if existing is not None:
            grouped[key] = existing.model_copy(
                update={"occurrence_count": existing.occurrence_count + 1}
            )
            continue
        digest = sha256("|".join(key).encode("utf-8")).hexdigest()[:16]
        sanitized_evidence = _sanitize_text(raw.evidence) if raw.evidence else None
        grouped[key] = Finding(
            finding_id=f"finding-{digest}",
            check_code=raw.check_code,
            category=CATEGORY_BY_CODE[raw.check_code],
            source_page=page,
            target_url=target,
            signature=signature,
            message=_bounded(_sanitize_text(raw.message), 500),
            evidence=_bounded(sanitized_evidence, 1_000) if sanitized_evidence else None,
        )
    return sorted(grouped.values(), key=lambda item: item.finding_id)
