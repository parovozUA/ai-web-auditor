from ai_web_auditor.checks import (
    collect_seo_observations,
    normalize_findings,
)
from ai_web_auditor.models import FindingCode, RawObservation


def test_blank_seo_values_create_expected_findings() -> None:
    observations = collect_seo_observations(
        page_url="https://example.com/",
        title="  ",
        h1_count=0,
        meta_description=None,
    )

    assert {item.check_code for item in observations} == {
        FindingCode.TITLE_MISSING,
        FindingCode.H1_MISSING,
        FindingCode.META_DESCRIPTION_MISSING,
    }


def test_related_scan_runs_only_enabled_seo_codes() -> None:
    observations = collect_seo_observations(
        page_url="https://example.com/a",
        title="",
        h1_count=0,
        meta_description="",
        enabled_codes={FindingCode.TITLE_MISSING},
    )

    assert [item.check_code for item in observations] == [FindingCode.TITLE_MISSING]


def test_normalizer_masks_queries_deduplicates_and_counts_occurrences() -> None:
    observation = RawObservation(
        check_code=FindingCode.RESOURCE_HTTP_ERROR,
        page_url="https://example.com/a?session=secret",
        target_url="https://cdn.example.com/x.js?token=secret",
        message="Resource returned HTTP 500",
        signature="resource_http_error:/x.js:500",
        evidence="https://cdn.example.com/x.js?token=secret",
    )

    findings = normalize_findings([observation, observation])

    assert len(findings) == 1
    assert findings[0].occurrence_count == 2
    assert "secret" not in findings[0].model_dump_json()


def test_finding_id_is_stable_for_equivalent_input() -> None:
    observation = RawObservation(
        check_code=FindingCode.CONSOLE_ERROR,
        page_url="https://example.com/",
        message="Widget failed at line 19",
        signature="widget failed at line <n>",
    )

    assert (
        normalize_findings([observation])[0].finding_id
        == normalize_findings([observation])[0].finding_id
    )
