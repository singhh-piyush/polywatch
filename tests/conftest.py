import pytest

from polywatch.api.http import Http


@pytest.fixture
async def http():
    client = Http(max_tries=2, backoff=0)
    yield client
    await client.aclose()
