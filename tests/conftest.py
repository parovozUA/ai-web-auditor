from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio

from ai_web_auditor.link_checker import LinkChecker
from ai_web_auditor.scanner import PageScanner
from ai_web_auditor.urls import UrlPolicy
from tests.fixture_site import FixtureSite


@pytest.fixture(scope="session")
def fixture_site() -> Iterator[FixtureSite]:
    with FixtureSite() as site:
        yield site


@pytest_asyncio.fixture
async def page_scanner() -> AsyncIterator[PageScanner]:
    policy = UrlPolicy(allow_private=True)
    checker = LinkChecker(policy=policy)
    async with PageScanner(policy=policy, link_checker=checker) as scanner:
        yield scanner
