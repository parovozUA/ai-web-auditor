import pytest

from tests.fixture_site import FixtureSite
from website_reliability_agent.models import FindingCode, ScanStatus
from website_reliability_agent.scanner import PageScanner
from website_reliability_agent.urls import origin_of

pytestmark = pytest.mark.integration


async def test_clean_page_has_no_findings(
    page_scanner: PageScanner,
    fixture_site: FixtureSite,
) -> None:
    result = await page_scanner.scan(fixture_site.url("/clean"))

    assert result.observation.scan_status is ScanStatus.COMPLETED
    assert result.findings == []


async def test_scanner_captures_seo_console_link_and_resource_failures(
    page_scanner: PageScanner,
    fixture_site: FixtureSite,
) -> None:
    seo = await page_scanner.scan(fixture_site.url("/seo"))
    js = await page_scanner.scan(fixture_site.url("/js"))
    network = await page_scanner.scan(fixture_site.url("/link-resource"))

    assert {item.check_code for item in seo.findings} >= {
        FindingCode.TITLE_MISSING,
        FindingCode.META_DESCRIPTION_MISSING,
    }
    assert FindingCode.CONSOLE_ERROR in {item.check_code for item in js.findings}
    assert {item.check_code for item in network.findings} >= {
        FindingCode.BROKEN_INTERNAL_LINK,
        FindingCode.RESOURCE_HTTP_ERROR,
    }


async def test_related_scan_filters_to_seed_codes(
    page_scanner: PageScanner,
    fixture_site: FixtureSite,
) -> None:
    result = await page_scanner.scan(
        fixture_site.url("/mixed/a"),
        enabled_codes={FindingCode.TITLE_MISSING},
        expected_origin=origin_of(fixture_site.url("/")),
    )

    assert {item.check_code for item in result.findings} == {
        FindingCode.TITLE_MISSING
    }
