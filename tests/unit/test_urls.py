import pytest

from ai_web_auditor.urls import (
    UnsafeUrlError,
    UrlPolicy,
    canonicalize_url,
    sanitize_url,
    select_related_urls,
)


def test_sanitize_url_masks_query_values_and_drops_fragment() -> None:
    assert (
        sanitize_url("https://Example.com/a?token=secret&x=1#part")
        == "https://example.com/a?token=%2A%2A%2A&x=%2A%2A%2A"
    )


def test_related_urls_are_same_origin_unique_and_in_dom_order() -> None:
    anchors = [
        "/first#x",
        "https://example.com/first",
        "mailto:test@example.com",
        "https://other.example/page",
        "/image.png",
        "/second",
        "/third",
        "/fourth",
        "/fifth",
        "/sixth",
    ]

    assert select_related_urls(
        anchors,
        base_url="https://example.com/start",
        seed_url="https://example.com/start",
    ) == [
        "https://example.com/first",
        "https://example.com/second",
        "https://example.com/third",
        "https://example.com/fourth",
        "https://example.com/fifth",
    ]


def test_canonicalize_url_normalizes_default_port() -> None:
    assert canonicalize_url("HTTPS://Example.com:443/a#b") == "https://example.com/a"


async def public_resolver(host: str, port: int | None) -> list[str]:
    return ["93.184.216.34"]


async def private_resolver(host: str, port: int | None) -> list[str]:
    return ["127.0.0.1"]


async def test_policy_rejects_credentials() -> None:
    policy = UrlPolicy(resolver=public_resolver)
    with pytest.raises(UnsafeUrlError, match="credentials"):
        await policy.validate("https://user:pass@example.com/")


async def test_policy_rejects_private_dns_resolution() -> None:
    policy = UrlPolicy(resolver=private_resolver)
    with pytest.raises(UnsafeUrlError, match="network"):
        await policy.validate("https://example.test/")


async def test_policy_allows_private_only_when_explicit() -> None:
    policy = UrlPolicy(allow_private=True, resolver=private_resolver)
    assert await policy.validate("http://127.0.0.1:8000/a") == "http://127.0.0.1:8000/a"


async def test_policy_enforces_expected_origin() -> None:
    policy = UrlPolicy(resolver=public_resolver)
    with pytest.raises(UnsafeUrlError, match="origin"):
        await policy.validate(
            "https://other.example/",
            expected_origin=("https", "example.com", 443),
        )
