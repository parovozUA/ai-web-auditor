from dataclasses import dataclass
from time import perf_counter
from typing import Any
from urllib.parse import urlparse

from playwright.async_api import (
    Browser,
    ConsoleMessage,
    Page,
    Request,
    Response,
    async_playwright,
)
from playwright.async_api import (
    Error as PlaywrightError,
)

from ai_web_auditor.checks import collect_seo_observations, normalize_findings
from ai_web_auditor.link_checker import LinkChecker
from ai_web_auditor.models import (
    FindingCode,
    PageObservation,
    PageScanResult,
    RawObservation,
    ScanStatus,
)
from ai_web_auditor.urls import (
    Origin,
    UnsafeUrlError,
    UrlPolicy,
    canonicalize_url,
    internal_http_links,
    sanitize_url,
)


@dataclass
class _ScanAccumulator:
    requested_url: str
    final_url: str = ""
    main_document_status: int | None = None
    scan_status: ScanStatus = ScanStatus.FAILED
    operational_error: str | None = None
    raw_observations: list[RawObservation] | None = None
    internal_links: list[str] | None = None

    def __post_init__(self) -> None:
        if self.raw_observations is None:
            self.raw_observations = []
        if self.internal_links is None:
            self.internal_links = []


class PageScanner:
    """Deterministic, instrumented single-page Playwright scanner."""

    def __init__(
        self,
        policy: UrlPolicy,
        link_checker: LinkChecker,
        timeout_ms: int = 15_000,
    ) -> None:
        self._policy = policy
        self._link_checker = link_checker
        self._timeout_ms = timeout_ms
        self._playwright: Any = None
        self._browser: Browser | None = None

    async def __aenter__(self) -> "PageScanner":
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=True)
        return self

    async def __aexit__(self, *args: object) -> None:
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    async def scan(
        self,
        url: str,
        *,
        enabled_codes: set[FindingCode] | None = None,
        expected_origin: Origin | None = None,
    ) -> PageScanResult:
        if self._browser is None:
            raise RuntimeError("PageScanner is not open. Use 'async with PageScanner(...):'")

        started_at = perf_counter()
        canonical_requested = canonicalize_url(url)
        sanitized_requested = sanitize_url(canonical_requested)
        accumulator = _ScanAccumulator(requested_url=sanitized_requested)

        # Pre-flight guard
        try:
            await self._policy.validate(
                canonical_requested,
                expected_origin=expected_origin,
            )
        except UnsafeUrlError as exc:
            accumulator.scan_status = ScanStatus.FAILED
            accumulator.operational_error = str(exc)
            obs = RawObservation(
                check_code=FindingCode.NAVIGATION_FAILED,
                page_url=sanitized_requested,
                message=f"URL rejected by policy: {exc}",
                signature="navigation_failed:url_policy_rejected",
                evidence=sanitized_requested,
            )
            accumulator.raw_observations.append(obs)  # type: ignore[union-attr]
            elapsed_ms = round((perf_counter() - started_at) * 1_000)
            page_obs = PageObservation(
                requested_url=sanitized_requested,
                final_url=sanitized_requested,
                scan_status=accumulator.scan_status,
                elapsed_ms=elapsed_ms,
                operational_error=accumulator.operational_error,
                raw_observations=accumulator.raw_observations,  # type: ignore[arg-type]
            )
            findings = normalize_findings(page_obs.raw_observations)
            return PageScanResult(observation=page_obs, findings=findings)

        context = await self._browser.new_context()
        page = await context.new_page()

        try:
            # Route guard for SSRF inside Playwright
            async def _route_guard(route: Any, request: Request) -> None:
                req_url = request.url
                parsed = urlparse(req_url)
                if parsed.scheme in {"http", "https"}:
                    try:
                        await self._policy.validate(req_url)
                    except UnsafeUrlError:
                        await route.abort("blockedbyclient")
                        return
                await route.continue_()

            await page.route("**/*", _route_guard)

            # Listeners
            page.on(
                "pageerror",
                lambda exc: self._on_pageerror(exc, accumulator, page),
            )
            page.on(
                "console",
                lambda msg: self._on_console(msg, accumulator, page),
            )
            page.on(
                "requestfailed",
                lambda req: self._on_requestfailed(req, accumulator, page),
            )
            page.on(
                "response",
                lambda resp: self._on_response(resp, accumulator, page, canonical_requested),
            )

            # Navigate
            try:
                response = await page.goto(
                    canonical_requested,
                    timeout=self._timeout_ms,
                    wait_until="load",
                )
                if response is not None:
                    accumulator.main_document_status = response.status
                    final_url = page.url
                    accumulator.final_url = sanitize_url(final_url)
                    accumulator.scan_status = ScanStatus.COMPLETED
                else:
                    accumulator.final_url = sanitize_url(page.url or canonical_requested)
                    accumulator.scan_status = ScanStatus.COMPLETED
            except PlaywrightError as exc:
                accumulator.scan_status = ScanStatus.FAILED
                accumulator.operational_error = str(exc)
                accumulator.final_url = sanitize_url(page.url or canonical_requested)
                obs = RawObservation(
                    check_code=FindingCode.NAVIGATION_FAILED,
                    page_url=accumulator.final_url,
                    message=f"Navigation failed: {exc}",
                    signature="navigation_failed:playwright_error",
                    evidence=accumulator.final_url,
                )
                accumulator.raw_observations.append(obs)  # type: ignore[union-attr]

            if accumulator.scan_status is ScanStatus.COMPLETED:
                # SEO checks
                try:
                    title_elem = await page.eval_on_selector("title", "el => el.textContent")
                except Exception:
                    title_elem = None

                try:
                    meta_desc = await page.eval_on_selector(
                        'meta[name="description" i]',
                        'el => el.getAttribute("content")',
                    )
                except Exception:
                    meta_desc = None

                h1_texts: list[str] = []
                try:
                    h1_texts = await page.eval_on_selector_all(
                        "h1", "els => els.map(e => e.textContent || '')"
                    )
                except Exception:
                    h1_texts = []

                seo_obs = collect_seo_observations(
                    page_url=accumulator.final_url,
                    title=title_elem if isinstance(title_elem, str) else None,
                    h1_count=len(h1_texts),
                    meta_description=meta_desc if isinstance(meta_desc, str) else None,
                    enabled_codes=enabled_codes,
                )
                accumulator.raw_observations.extend(seo_obs)  # type: ignore[union-attr]

                # Internal links extraction
                anchors: list[str] = []
                try:
                    anchors = await page.eval_on_selector_all(
                        "a[href]", "els => els.map(e => e.getAttribute('href'))"
                    )
                except Exception:
                    anchors = []

                valid_internal_links = internal_http_links(
                    [a for a in anchors if isinstance(a, str)],
                    base_url=accumulator.final_url,
                    seed_url=canonical_requested,
                )
                accumulator.internal_links = valid_internal_links

                # Link checking
                link_obs = await self._link_checker.check(
                    page_url=accumulator.final_url,
                    anchors=[a for a in anchors if isinstance(a, str)],
                    enabled_codes=enabled_codes,
                )
                accumulator.raw_observations.extend(link_obs)  # type: ignore[union-attr]

        finally:
            await page.close()
            await context.close()

        elapsed_ms = round((perf_counter() - started_at) * 1_000)
        page_obs = PageObservation(
            requested_url=accumulator.requested_url,
            final_url=accumulator.final_url,
            scan_status=accumulator.scan_status,
            main_document_status=accumulator.main_document_status,
            elapsed_ms=elapsed_ms,
            operational_error=accumulator.operational_error,
            raw_observations=accumulator.raw_observations,  # type: ignore[arg-type]
            internal_links=accumulator.internal_links,  # type: ignore[arg-type]
        )

        findings = normalize_findings(page_obs.raw_observations)

        return PageScanResult(observation=page_obs, findings=findings)

    def _on_pageerror(
        self,
        exc: PlaywrightError,
        acc: _ScanAccumulator,
        page: Page,
    ) -> None:
        url = sanitize_url(page.url or acc.requested_url)
        msg = str(exc)
        obs = RawObservation(
            check_code=FindingCode.CONSOLE_ERROR,
            page_url=url,
            message=f"Uncaught JavaScript exception: {msg}",
            signature=f"console_error:{msg}",
            evidence=msg,
        )
        acc.raw_observations.append(obs)  # type: ignore[union-attr]

    def _on_console(
        self,
        msg: ConsoleMessage,
        acc: _ScanAccumulator,
        page: Page,
    ) -> None:
        if msg.type == "error":
            url = sanitize_url(page.url or acc.requested_url)
            text = msg.text
            obs = RawObservation(
                check_code=FindingCode.CONSOLE_ERROR,
                page_url=url,
                message=f"Console error: {text}",
                signature=f"console_error:{text}",
                evidence=text,
            )
            acc.raw_observations.append(obs)  # type: ignore[union-attr]

    def _on_requestfailed(
        self,
        req: Request,
        acc: _ScanAccumulator,
        page: Page,
    ) -> None:
        if req.is_navigation_request():
            return
        url = sanitize_url(page.url or acc.requested_url)
        target = sanitize_url(req.url)
        failure = req.failure
        reason = failure if failure else "Failed to load resource"
        obs = RawObservation(
            check_code=FindingCode.RESOURCE_HTTP_ERROR,
            page_url=url,
            target_url=target,
            message=f"Resource request failed: {target} ({reason})",
            signature=f"resource_http_error:{target}",
            evidence=str(reason),
        )
        acc.raw_observations.append(obs)  # type: ignore[union-attr]

    def _on_response(
        self,
        resp: Response,
        acc: _ScanAccumulator,
        page: Page,
        seed_url: str,
    ) -> None:
        req = resp.request
        if req.is_navigation_request():
            return
        status = resp.status
        if status >= 400:
            url = sanitize_url(page.url or acc.requested_url)
            target = sanitize_url(resp.url)
            obs = RawObservation(
                check_code=FindingCode.RESOURCE_HTTP_ERROR,
                page_url=url,
                target_url=target,
                message=f"Resource HTTP error {status}: {target}",
                signature=f"resource_http_error:{target}:{status}",
                evidence=f"status={status}",
            )
            acc.raw_observations.append(obs)  # type: ignore[union-attr]
