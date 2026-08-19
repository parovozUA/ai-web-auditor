import asyncio
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

from ai_web_auditor.models import FindingCode, RawObservation
from ai_web_auditor.urls import (
    Origin,
    UnsafeUrlError,
    UrlPolicy,
    internal_http_links,
    origin_of,
)


@dataclass(frozen=True, slots=True)
class LinkResult:
    status_code: int | None
    final_url: str
    error_category: str | None = None


def _to_observation(page_url: str, target_url: str, result: LinkResult) -> RawObservation | None:
    path = urlsplit(target_url).path or "/"
    if result.status_code is not None and result.status_code >= 400:
        return RawObservation(
            check_code=FindingCode.BROKEN_INTERNAL_LINK,
            page_url=page_url,
            target_url=target_url,
            message=f"Internal link returned HTTP {result.status_code}",
            signature=f"broken_internal_link:{path}:{result.status_code}",
            evidence=target_url,
        )
    if result.error_category is not None and result.error_category != "out_of_scope_redirect":
        return RawObservation(
            check_code=FindingCode.BROKEN_INTERNAL_LINK,
            page_url=page_url,
            target_url=target_url,
            message=f"Internal link request failed ({result.error_category})",
            signature=f"broken_internal_link:{path}:{result.error_category.lower()}",
            evidence=target_url,
        )
    return None


class LinkChecker:
    """Bounded, cached HTTP link validator for same-origin links."""

    def __init__(
        self,
        *,
        policy: UrlPolicy,
        timeout_seconds: float = 10.0,
        max_links: int = 50,
        concurrency: int = 10,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._policy = policy
        self._timeout = timeout_seconds
        self._max_links = min(max_links, 50)
        self._semaphore = asyncio.Semaphore(min(concurrency, 10))
        self._transport = transport
        self._cache: dict[str, LinkResult] = {}

    async def _request_with_redirects(
        self,
        client: httpx.AsyncClient,
        url: str,
        expected_origin: Origin,
    ) -> LinkResult:
        current = url
        for _ in range(6):
            try:
                current = await self._policy.validate(
                    current,
                    expected_origin=expected_origin,
                )
            except UnsafeUrlError:
                return LinkResult(None, current, "out_of_scope_redirect")
            try:
                response = await client.get(current)
            except httpx.RequestError as exc:
                return LinkResult(None, current, type(exc).__name__)
            if response.status_code not in {301, 302, 303, 307, 308}:
                return LinkResult(response.status_code, current)
            location = response.headers.get("location")
            if not location:
                return LinkResult(response.status_code, current)
            current = urljoin(current, location)
        return LinkResult(None, current, "too_many_redirects")

    async def _check_cached(
        self,
        client: httpx.AsyncClient,
        url: str,
        expected_origin: Origin,
    ) -> LinkResult:
        if url in self._cache:
            return self._cache[url]
        async with self._semaphore:
            if url in self._cache:
                return self._cache[url]
            result = await self._request_with_redirects(client, url, expected_origin)
            self._cache[url] = result
            return result

    async def check(
        self,
        *,
        page_url: str,
        anchors: list[str],
        enabled_codes: set[FindingCode] | None = None,
    ) -> list[RawObservation]:
        if (
            enabled_codes is not None
            and FindingCode.BROKEN_INTERNAL_LINK not in enabled_codes
        ):
            return []
        links = internal_http_links(
            anchors,
            base_url=page_url,
            seed_url=page_url,
            limit=self._max_links,
            exclude_assets=False,
        )
        if not links:
            return []
        async with httpx.AsyncClient(
            timeout=self._timeout,
            follow_redirects=False,
            transport=self._transport,
        ) as client:
            results = await asyncio.gather(
                *(
                    self._check_cached(client, url, origin_of(page_url))
                    for url in links
                )
            )
        return [
            observation
            for url, result in zip(links, results, strict=True)
            if (observation := _to_observation(page_url, url, result)) is not None
        ]
