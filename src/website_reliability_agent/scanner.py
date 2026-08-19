from time import perf_counter
from urllib.parse import urlsplit

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    Route,
    async_playwright,
)
from playwright.async_api import (
    Error as PlaywrightError,
)
from playwright.async_api import (
    Request as PlaywrightRequest,
)
from playwright.async_api import (
    Response as PlaywrightResponse,
)

from website_reliability_agent.checks import collect_seo_observations, normalize_findings
from website_reliability_agent.link_checker import LinkChecker
from website_reliability_agent.models import (
    FindingCode,
    PageObservation,
    PageScanResult,
    RawObservation,
    ScanStatus,
)
from website_reliability_agent.urls import (
    Origin,
    UnsafeUrlError,
    UrlPolicy,
    canonicalize_url,
    internal_http_links,
    sanitize_url,
)

_RESOURCE_TYPES = {"script", "stylesheet", "image", "media", "font", "xhr", "fetch"}


def _navigation_http_error(page_url: str, status: int) -> RawObservation:
    return RawObservation(
        check_code=FindingCode.NAVIGATION_HTTP_ERROR,
        page_url=page_url,
        message=f"Main document returned HTTP {status}",
        signature=f"navigation_http_error:{status}",
        evidence=page_url,
    )


def _navigation_failed(page_url: str, error_category: str) -> RawObservation:
    return RawObservation(
        check_code=FindingCode.NAVIGATION_FAILED,
        page_url=page_url,
        message=f"Page navigation failed ({error_category})",
        signature=f"navigation_failed:{error_category.lower()}",
        evidence=page_url,
    )


class PageScanner:
    """Single-page Playwright scanner with network guardrails and event capture."""

    def __init__(
        self,
        *,
        policy: UrlPolicy,
        link_checker: LinkChecker,
        navigation_timeout_ms: int = 15_000,
    ) -> None:
        self._policy = policy
        self._link_checker = link_checker
        self._navigation_timeout_ms = navigation_timeout_ms
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None

    async def __aenter__(self) -> "PageScanner":
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=True)
        self._context = await self._browser.new_context()
        await self._context.route("**/*", self._guard_route)
        return self

    async def __aexit__(self, *args: object) -> None:
        if self._context is not None:
            await self._context.close()
        if self._browser is not None:
            await self._browser.close()
        if self._playwright is not None:
            await self._playwright.stop()

    async def _guard_route(self, route: Route) -> None:
        url = route.request.url
        if url.startswith(("http://", "https://")):
            try:
                await self._policy.validate(url)
            except UnsafeUrlError:
                await route.abort()
                return
        await route.continue_()

    def _require_context(self) -> BrowserContext:
        if self._context is None:
            raise RuntimeError("PageScanner context is not active; use async with PageScanner(...)")
        return self._context

    def _attach_observers(
        self,
        page: Page,
        target_list: list[RawObservation],
        current_page_url: str,
        enabled_codes: set[FindingCode] | None,
    ) -> None:
        def on_page_error(error: Exception) -> None:
            if enabled_codes is None or FindingCode.PAGE_ERROR in enabled_codes:
                msg = str(error)
                target_list.append(
                    RawObservation(
                        check_code=FindingCode.PAGE_ERROR,
                        page_url=current_page_url,
                        message=msg,
                        signature=msg,
                        evidence=msg,
                    )
                )

        def on_console(msg: object) -> None:
            if getattr(msg, "type", None) == "error":
                if enabled_codes is None or FindingCode.CONSOLE_ERROR in enabled_codes:
                    text = getattr(msg, "text", "") or ""
                    target_list.append(
                        RawObservation(
                            check_code=FindingCode.CONSOLE_ERROR,
                            page_url=current_page_url,
                            message=text,
                            signature=text,
                            evidence=text,
                        )
                    )

        def on_request_failed(request: PlaywrightRequest) -> None:
            if request.resource_type in _RESOURCE_TYPES:
                if enabled_codes is None or FindingCode.REQUEST_FAILED in enabled_codes:
                    failure = request.failure or "Request failed"
                    target_url = request.url
                    path = urlsplit(target_url).path or "/"
                    target_list.append(
                        RawObservation(
                            check_code=FindingCode.REQUEST_FAILED,
                            page_url=current_page_url,
                            target_url=target_url,
                            message=f"Resource request failed: {failure}",
                            signature=f"request_failed:{path}",
                            evidence=target_url,
                        )
                    )

        def on_response(response: PlaywrightResponse) -> None:
            request = response.request
            if request.resource_type in _RESOURCE_TYPES and response.status >= 400:
                if enabled_codes is None or FindingCode.RESOURCE_HTTP_ERROR in enabled_codes:
                    target_url = response.url
                    path = urlsplit(target_url).path or "/"
                    target_list.append(
                        RawObservation(
                            check_code=FindingCode.RESOURCE_HTTP_ERROR,
                            page_url=current_page_url,
                            target_url=target_url,
                            message=f"Resource returned HTTP {response.status}",
                            signature=f"resource_http_error:{path}:{response.status}",
                            evidence=target_url,
                        )
                    )

        page.on("pageerror", on_page_error)
        page.on("console", on_console)
        page.on("requestfailed", on_request_failed)
        page.on("response", on_response)

    async def scan(
        self,
        url: str,
        *,
        enabled_codes: set[FindingCode] | None = None,
        expected_origin: Origin | None = None,
    ) -> PageScanResult:
        requested_url = await self._policy.validate(url, expected_origin=expected_origin)
        started = perf_counter()
        raw: list[RawObservation] = []
        final_url = requested_url
        status: int | None = None
        internal_links: list[str] = []
        error_category: str | None = None

        page = await self._require_context().new_page()
        try:
            self._attach_observers(page, raw, requested_url, enabled_codes)
            response = await page.goto(
                requested_url,
                wait_until="load",
                timeout=self._navigation_timeout_ms,
            )
            final_url = await self._policy.validate(
                page.url,
                expected_origin=expected_origin,
            )
            status = response.status if response is not None else None
            if status is not None and status >= 400:
                raw.append(_navigation_http_error(final_url, status))
                scan_status = ScanStatus.FAILED
            else:
                scan_status = ScanStatus.COMPLETED

                # Title
                try:
                    title = await page.title()
                except Exception:
                    title = None

                # H1 count
                try:
                    h1_count = await page.locator("h1").count()
                except Exception:
                    h1_count = 0

                # Meta description
                try:
                    meta_locator = page.locator("meta[name='description']")
                    if await meta_locator.count() > 0:
                        meta_description = await meta_locator.first.get_attribute("content")
                    else:
                        meta_description = None
                except Exception:
                    meta_description = None

                seo_checks = collect_seo_observations(
                    page_url=final_url,
                    title=title,
                    h1_count=h1_count,
                    meta_description=meta_description,
                    enabled_codes=enabled_codes,
                )
                raw.extend(seo_checks)

                # Anchors
                try:
                    anchors = await page.locator("a").evaluate_all(
                        "elements => elements.map(e => e.getAttribute('href')).filter(Boolean)"
                    )
                except Exception:
                    anchors = []

                internal_links = internal_http_links(
                    anchors,
                    base_url=final_url,
                    seed_url=final_url,
                    limit=50,
                )

                link_checks = await self._link_checker.check(
                    page_url=final_url,
                    anchors=anchors,
                    enabled_codes=enabled_codes,
                )
                raw.extend(link_checks)

        except (TimeoutError, PlaywrightError, UnsafeUrlError) as exc:
            try:
                page_raw_url = page.url
                raw_target = page_raw_url if page_raw_url.startswith("http") else requested_url
                final_url = canonicalize_url(
                    raw_target
                )
            except Exception:
                final_url = canonicalize_url(
                    requested_url
                )
            status = None
            scan_status = ScanStatus.FAILED
            error_category = type(exc).__name__
            raw.append(_navigation_failed(final_url, error_category))
        finally:
            await page.close()

        # Filter raw observations to enabled codes (navigation failures are always kept)
        filtered_raw = [
            item for item in raw
            if enabled_codes is None
            or item.check_code in enabled_codes
            or item.check_code in {FindingCode.NAVIGATION_FAILED, FindingCode.NAVIGATION_HTTP_ERROR}
        ]

        observation = PageObservation(
            requested_url=sanitize_url(requested_url),
            final_url=sanitize_url(final_url),
            scan_status=scan_status,
            main_document_status=status,
            elapsed_ms=round((perf_counter() - started) * 1_000),
            internal_links=internal_links,
            raw_observations=filtered_raw,
            operational_error=error_category,
        )
        return PageScanResult(
            observation=observation,
            findings=normalize_findings(filtered_raw),
        )
