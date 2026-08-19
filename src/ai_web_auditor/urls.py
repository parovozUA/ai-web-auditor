import asyncio
import os
import socket
from collections.abc import Awaitable, Callable, Sequence
from ipaddress import ip_address
from urllib.parse import parse_qsl, quote, unquote, urlencode, urljoin, urlsplit, urlunsplit

Origin = tuple[str, str, int]
Resolver = Callable[[str, int | None], Awaitable[Sequence[str]]]


ASSET_SUFFIXES = {
    ".7z", ".avi", ".css", ".csv", ".doc", ".docx", ".gif", ".gz",
    ".ico", ".jpeg", ".jpg", ".js", ".json", ".m4a", ".mov", ".mp3",
    ".mp4", ".pdf", ".png", ".rar", ".svg", ".tar", ".tgz", ".txt",
    ".wav", ".webm", ".webp", ".woff", ".woff2", ".xls", ".xlsx", ".xml",
    ".zip",
}


class UnsafeUrlError(ValueError):
    """Raised when a URL violates safety or origin policies."""


def default_port_for_scheme(scheme: str) -> int:
    if scheme == "http":
        return 80
    if scheme == "https":
        return 443
    raise UnsafeUrlError(f"Unsupported scheme: {scheme}")


def canonicalize_url(url: str) -> str:
    """Canonicalize a URL: lowercase scheme/host, strip default port and fragment."""
    stripped = url.strip()
    if not stripped:
        raise UnsafeUrlError("URL cannot be empty")

    parsed = urlsplit(stripped)
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise UnsafeUrlError(f"Scheme must be http or https, got {parsed.scheme!r}")

    if parsed.username or parsed.password:
        raise UnsafeUrlError("Embedded credentials are not allowed")

    if not parsed.hostname:
        raise UnsafeUrlError("URL must include a host")

    host = parsed.hostname.lower()
    port = parsed.port

    default_port = default_port_for_scheme(scheme)
    if port == default_port:
        port = None

    netloc = host if port is None else f"{host}:{port}"
    path = parsed.path or "/"

    return urlunsplit((scheme, netloc, path, parsed.query, ""))


def origin_of(url: str) -> Origin:
    """Return the normalized (scheme, host, port) of a URL."""
    canonical = canonicalize_url(url)
    parsed = urlsplit(canonical)
    scheme = parsed.scheme.lower()
    host = parsed.hostname or ""
    port = parsed.port or default_port_for_scheme(scheme)
    return (scheme, host, port)


def sanitize_url(url: str) -> str:
    """Canonicalize the URL and mask all query parameter values."""
    canonical = canonicalize_url(url)
    parsed = urlsplit(canonical)
    if not parsed.query:
        return canonical

    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    masked_pairs = [(k, "***") for k, _ in pairs]
    masked_query = urlencode(
        masked_pairs,
        quote_via=lambda s, safe="", encoding=None, errors=None: quote(s, safe=""),
    )
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, masked_query, ""))


def _is_asset_path(path: str) -> bool:
    _, ext = os.path.splitext(path.lower())
    return ext in ASSET_SUFFIXES


def internal_http_links(
    anchors: Sequence[str],
    *,
    base_url: str,
    seed_url: str,
    limit: int = 50,
    exclude_assets: bool = False,
) -> list[str]:
    """Resolve and filter anchors to unique same-origin HTTP(S) URLs in encounter order."""
    seed_origin = origin_of(seed_url)
    canonical_seed = canonicalize_url(seed_url)
    seen: set[str] = set()
    result: list[str] = []

    for raw_anchor in anchors:
        anchor = raw_anchor.strip()
        if not anchor:
            continue
        if anchor.lower().startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        try:
            resolved = urljoin(base_url, anchor)
            canonical = canonicalize_url(resolved)
        except UnsafeUrlError:
            continue

        if origin_of(canonical) != seed_origin:
            continue
        if canonical == canonical_seed:
            continue
        if exclude_assets:
            parsed_path = unquote(urlsplit(canonical).path)
            if _is_asset_path(parsed_path):
                continue

        if canonical not in seen:
            seen.add(canonical)
            result.append(canonical)
            if len(result) >= limit:
                break

    return result


def select_related_urls(
    anchors: list[str],
    *,
    base_url: str,
    seed_url: str,
    limit: int = 5,
) -> list[str]:
    """Select the first up-to-5 eligible same-origin internal links in DOM order."""
    return internal_http_links(
        anchors,
        base_url=base_url,
        seed_url=seed_url,
        limit=min(limit, 5),
        exclude_assets=True,
    )


async def resolve_host(host: str, port: int | None) -> list[str]:
    """Resolve host IP addresses asynchronously."""
    def _lookup() -> list[str]:
        try:
            infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
            return list({str(info[4][0]) for info in infos})
        except socket.gaierror:
            return []

    return await asyncio.to_thread(_lookup)


class UrlPolicy:
    """Enforces SSRF prevention, scheme validity, and origin boundaries."""

    def __init__(
        self,
        *,
        allow_private: bool = False,
        resolver: Resolver = resolve_host,
    ) -> None:
        self.allow_private = allow_private
        self._resolver = resolver
        self._resolution_cache: dict[tuple[str, int | None], list[str]] = {}

    async def _resolve(self, host: str, port: int | None) -> list[str]:
        key = (host, port)
        if key not in self._resolution_cache:
            self._resolution_cache[key] = list(await self._resolver(host, port))
        return self._resolution_cache[key]

    async def validate(
        self,
        url: str,
        *,
        expected_origin: Origin | None = None,
    ) -> str:
        canonical = canonicalize_url(url)
        parsed_origin = origin_of(canonical)

        if expected_origin is not None and parsed_origin != expected_origin:
            raise UnsafeUrlError("URL escaped the allowed origin")

        if not self.allow_private:
            addresses = await self._resolve(parsed_origin[1], parsed_origin[2])
            if not addresses or any(not ip_address(addr).is_global for addr in addresses):
                raise UnsafeUrlError("URL resolves to a blocked network target")

        return canonical
