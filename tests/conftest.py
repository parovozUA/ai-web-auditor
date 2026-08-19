from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio

from tests.fixture_site import FixtureSite
from website_reliability_agent.link_checker import LinkChecker
from website_reliability_agent.scanner import PageScanner
from website_reliability_agent.urls import UrlPolicy


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
