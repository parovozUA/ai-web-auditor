import httpx

from website_reliability_agent.link_checker import LinkChecker
from website_reliability_agent.models import FindingCode
from website_reliability_agent.urls import UrlPolicy


async def test_link_checker_reports_404_and_deduplicates_urls() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(404, request=request)

    checker = LinkChecker(
        policy=UrlPolicy(allow_private=True),
        transport=httpx.MockTransport(handler),
    )
    observations = await checker.check(
        page_url="https://example.com/",
        anchors=["/missing", "/missing#fragment"],
    )

    assert len(calls) == 1
    assert [item.check_code for item in observations] == [
        FindingCode.BROKEN_INTERNAL_LINK
    ]
    assert observations[0].signature == "broken_internal_link:/missing:404"


async def test_link_checker_checks_no_more_than_50_unique_links() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, request=request)

    checker = LinkChecker(
        policy=UrlPolicy(allow_private=True),
        transport=httpx.MockTransport(handler),
    )
    await checker.check(
        page_url="https://example.com/",
        anchors=[f"/page/{index}" for index in range(60)],
    )

    assert calls == 50


async def test_link_checker_reuses_per_run_cache_across_pages() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500, request=request)

    checker = LinkChecker(
        policy=UrlPolicy(allow_private=True),
        transport=httpx.MockTransport(handler),
    )
    first = await checker.check(page_url="https://example.com/a", anchors=["/shared"])
    second = await checker.check(page_url="https://example.com/b", anchors=["/shared"])

    assert calls == 1
    assert first[0].page_url.endswith("/a")
    assert second[0].page_url.endswith("/b")


async def test_link_checker_does_nothing_when_code_is_not_enabled() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("HTTP request must not be made")

    checker = LinkChecker(
        policy=UrlPolicy(allow_private=True),
        transport=httpx.MockTransport(handler),
    )

    assert await checker.check(
        page_url="https://example.com/",
        anchors=["/missing"],
        enabled_codes={FindingCode.TITLE_MISSING},
    ) == []


async def test_link_checker_does_not_follow_cross_origin_redirect() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            headers={"location": "https://other.example/private"},
            request=request,
        )

    checker = LinkChecker(
        policy=UrlPolicy(allow_private=True),
        transport=httpx.MockTransport(handler),
    )
    observations = await checker.check(
        page_url="https://example.com/",
        anchors=["/redirect"],
    )

    assert observations == []
