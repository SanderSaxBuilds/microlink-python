"""Live checks against api.microlink.io. The free plan has a small daily quota,
so these run only with MICROLINK_LIVE=1."""

import os

import pytest

from microlink import Microlink, RateLimitError

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("MICROLINK_LIVE") != "1", reason="set MICROLINK_LIVE=1 to call the real API"),
]


def test_live_metadata():
    try:
        page = Microlink(api_key=os.environ.get("MICROLINK_API_KEY")).metadata("https://github.com/microlinkhq")
    except RateLimitError:
        pytest.skip("free quota used up for today")
    assert page.status == "success"
    assert page.data["url"].startswith("https://github.com/")
    assert page.rate_limit.limit is not None
