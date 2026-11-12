"""Tests against responses recorded from api.microlink.io on 12 Nov 2026."""

import json
import pathlib
import urllib.parse

import pytest

from microlink import Microlink, MicrolinkError, RateLimitError, flatten_params

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


class FakeTransport:
    """Replays recorded responses in order and keeps the requested URLs."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, headers, timeout):
        self.calls.append((url, headers))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item["status"], item["headers"], json.dumps(item["body"]).encode()

    def query(self, i=-1):
        return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.calls[i][0]).query))


def client(*responses, **kw):
    t = FakeTransport(*responses)
    return Microlink(transport=t, sleep=lambda s: None, **kw), t


def test_metadata_parses_recorded_page():
    ml, t = client(load("meta"))
    page = ml.metadata("https://github.com/microlinkhq")
    assert page.status == "success"
    assert page.data["title"] == "microlink.io"
    assert page.data["publisher"] == "GitHub"
    assert page.data["logo"]["width"] == 512
    assert page.status_code == 200
    assert t.query() == {"url": "https://github.com/microlinkhq"}


def test_rate_limit_headers_are_read():
    ml, _ = client(load("meta"))
    page = ml.metadata("https://github.com/microlinkhq")
    assert page.rate_limit.limit == 5
    assert page.rate_limit.remaining == 4
    assert page.rate_limit.reset == 1794572566
    assert page.cache_status == "MISS"


def test_screenshot_builds_query_and_returns_asset_url():
    ml, t = client(load("shot"))
    shot = ml.screenshot("https://example.com")
    assert t.query() == {
        "url": "https://example.com",
        "screenshot": "true",
        "screenshot.type": "png",
        "meta": "false",
    }
    assert shot.asset_url("screenshot").endswith(".png")
    assert shot.data["screenshot"]["width"] == 2560
    assert shot.status_code is None


def test_screenshot_options_full_page_and_element():
    ml, t = client(load("shot"))
    ml.screenshot("https://example.com", full_page=True, element="main", type="jpeg", device="iPhone 15")
    q = t.query()
    assert q["screenshot.fullPage"] == "true"
    assert q["screenshot.element"] == "main"
    assert q["screenshot.type"] == "jpeg"
    assert q["device"] == "iPhone 15"


def test_extract_sends_rules_and_returns_only_rule_keys():
    ml, t = client(load("data"))
    out = ml.extract("https://example.com", {"heading": {"selector": "h1", "attr": "text"}})
    assert out == {"heading": None}
    q = t.query()
    assert q["data.heading.selector"] == "h1"
    assert q["data.heading.attr"] == "text"
    assert q["meta"] == "false"


def test_invalid_url_raises_with_code_and_help_link():
    ml, _ = client(load("bad"))
    with pytest.raises(MicrolinkError) as info:
        ml.get("notaurl")
    err = info.value
    assert err.code == "EINVALURL"
    assert err.http_status == 400
    assert err.more == "https://microlink.io/einvalurl"
    assert "not valid" in err.message


def test_server_error_is_retried_then_succeeds():
    five = {"status": 503, "headers": {}, "body": {"status": "error", "message": "busy"}}
    ml, t = client(five, load("meta"), retries=2)
    page = ml.metadata("https://github.com/microlinkhq")
    assert page.data["title"] == "microlink.io"
    assert len(t.calls) == 2


def test_server_error_after_all_retries_raises():
    five = {"status": 502, "headers": {}, "body": {"status": "error", "message": "bad gateway"}}
    ml, t = client(five, five, five, retries=2)
    with pytest.raises(MicrolinkError) as info:
        ml.get("https://example.com")
    assert info.value.http_status == 502
    assert len(t.calls) == 3


def test_network_error_is_retried():
    ml, t = client(ConnectionError("reset"), load("meta"), retries=1)
    assert ml.metadata("https://github.com/microlinkhq").status == "success"
    assert len(t.calls) == 2


def test_rate_limit_is_not_retried_and_carries_reset():
    limited = {
        "status": 429,
        "headers": {"x-rate-limit-limit": "50", "x-rate-limit-remaining": "0", "x-rate-limit-reset": "1794600000"},
        "body": {"status": "fail", "code": "ERATE", "message": "You have been rate limited."},
    }
    ml, t = client(limited, retries=3)
    with pytest.raises(RateLimitError) as info:
        ml.get("https://example.com")
    assert info.value.reset == 1794600000
    assert len(t.calls) == 1


def test_api_key_switches_to_pro_endpoint_and_header():
    ml, t = client(load("meta"), api_key="secret")
    ml.metadata("https://github.com/microlinkhq")
    url, headers = t.calls[0]
    assert url.startswith("https://pro.microlink.io/?")
    assert headers["x-api-key"] == "secret"


def test_no_key_uses_free_endpoint_without_key_header():
    ml, t = client(load("meta"))
    ml.metadata("https://github.com/microlinkhq")
    url, headers = t.calls[0]
    assert url.startswith("https://api.microlink.io/?")
    assert "x-api-key" not in headers


def test_non_json_body_raises_clear_error():
    ml = Microlink(transport=lambda u, h, t: (200, {}, b"<html>"), sleep=lambda s: None)
    with pytest.raises(MicrolinkError, match="not JSON"):
        ml.get("https://example.com")


def test_flatten_params_handles_nested_bools_lists_and_none():
    assert flatten_params(
        {"pdf": {"format": "Letter", "landscape": True}, "waitForSelector": None, "scripts": ["a.js", "b.js"]}
    ) == {"pdf": "true", "pdf.format": "Letter", "pdf.landscape": "true", "scripts": "a.js,b.js"}


def test_empty_url_and_empty_rules_are_rejected():
    ml, _ = client()
    with pytest.raises(ValueError):
        ml.get("")
    with pytest.raises(ValueError):
        ml.extract("https://example.com", {})
