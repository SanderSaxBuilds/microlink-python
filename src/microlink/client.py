"""Small Python client for the Microlink API (https://microlink.io/docs/api).

Uses only the standard library. Works without a key on the free plan,
pass ``api_key`` to use the Pro endpoint.
"""

from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

__all__ = [
    "Microlink",
    "MicrolinkResponse",
    "RateLimit",
    "MicrolinkError",
    "RateLimitError",
    "flatten_params",
]

FREE_URL = "https://api.microlink.io/"
PRO_URL = "https://pro.microlink.io/"
USER_AGENT = "microlink-python/0.1.0"

# A transport takes (url, headers, timeout) and returns (status, headers, body bytes).
Transport = Callable[[str, Dict[str, str], float], Tuple[int, Dict[str, str], bytes]]


class MicrolinkError(Exception):
    """The API answered with status "fail" or "error", or the call could not be made."""

    def __init__(
        self,
        message: str,
        *,
        code: Optional[str] = None,
        http_status: Optional[int] = None,
        more: Optional[str] = None,
        details: Any = None,
    ) -> None:
        super().__init__(f"{code}: {message}" if code else message)
        self.message = message
        self.code = code
        self.http_status = http_status
        self.more = more
        self.details = details


class RateLimitError(MicrolinkError):
    """The plan quota is used up (HTTP 429). ``reset`` is a Unix time when known."""

    def __init__(self, message: str, *, reset: Optional[int] = None, **kw: Any) -> None:
        super().__init__(message, **kw)
        self.reset = reset


@dataclass(frozen=True)
class RateLimit:
    limit: Optional[int]
    remaining: Optional[int]
    reset: Optional[int]

    @classmethod
    def from_headers(cls, headers: Mapping[str, str]) -> "RateLimit":
        def num(name: str) -> Optional[int]:
            value = headers.get(name)
            try:
                return int(value) if value is not None else None
            except ValueError:
                return None

        return cls(num("x-rate-limit-limit"), num("x-rate-limit-remaining"), num("x-rate-limit-reset"))


@dataclass(frozen=True)
class MicrolinkResponse:
    status: str
    data: Dict[str, Any]
    status_code: Optional[int]
    rate_limit: RateLimit
    cache_status: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    def asset_url(self, name: str) -> Optional[str]:
        """URL of a generated asset such as ``screenshot`` or ``pdf``, if present."""
        item = self.data.get(name)
        return item.get("url") if isinstance(item, dict) else None


def _encode(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def flatten_params(params: Mapping[str, Any], prefix: str = "") -> Dict[str, str]:
    """Turn nested options into Microlink's dotted query keys.

    ``{"screenshot": {"fullPage": True}}`` becomes ``{"screenshot.fullPage": "true"}``.
    A nested dict under ``screenshot``, ``pdf`` and similar also switches the
    feature on, the same way the official JavaScript client does.
    """
    out: Dict[str, str] = {}
    for key, value in params.items():
        if value is None:
            continue
        name = f"{prefix}.{key}" if prefix else key
        if isinstance(value, Mapping):
            if prefix == "" and key in {"screenshot", "pdf", "video", "audio", "palette", "insights", "iframe"}:
                out[name] = "true"
            out.update(flatten_params(value, name))
        elif isinstance(value, (list, tuple)):
            out[name] = ",".join(_encode(v) for v in value)
        else:
            out[name] = _encode(value)
    return out


def _urllib_transport(url: str, headers: Dict[str, str], timeout: float) -> Tuple[int, Dict[str, str], bytes]:
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read()
    except urllib.error.HTTPError as err:
        return err.code, {k.lower(): v for k, v in err.headers.items()}, err.read()


class Microlink:
    """Client for https://api.microlink.io (free) or https://pro.microlink.io (with a key).

    >>> ml = Microlink()
    >>> page = ml.metadata("https://github.com/microlinkhq")
    >>> page.data["title"]
    'microlink.io'
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        timeout: float = 30.0,
        retries: int = 2,
        backoff: float = 0.5,
        base_url: Optional[str] = None,
        transport: Optional[Transport] = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.api_key = api_key
        self.timeout = timeout
        self.retries = max(0, retries)
        self.backoff = backoff
        self.base_url = base_url or (PRO_URL if api_key else FREE_URL)
        self._transport = transport or _urllib_transport
        self._sleep = sleep

    # Core call ---------------------------------------------------------

    def get(self, url: str, **options: Any) -> MicrolinkResponse:
        """Call the API for ``url`` with any Microlink parameters as keyword arguments.

        Nested dicts become dotted keys, ``data`` takes extraction rules.
        """
        if not url or not isinstance(url, str):
            raise ValueError("url must be a non-empty string")
        query = {"url": url, **flatten_params(options)}
        full = self.base_url + "?" + urllib.parse.urlencode(query)
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        if self.api_key:
            headers["x-api-key"] = self.api_key

        attempt = 0
        while True:
            try:
                status, resp_headers, body = self._transport(full, headers, self.timeout)
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as err:
                if attempt < self.retries:
                    self._wait(attempt)
                    attempt += 1
                    continue
                raise MicrolinkError(f"request failed: {err}") from err

            if status >= 500 and attempt < self.retries:
                self._wait(attempt)
                attempt += 1
                continue
            return self._parse(status, resp_headers, body)

    def _wait(self, attempt: int) -> None:
        self._sleep(self.backoff * (2 ** attempt) + random.uniform(0, self.backoff / 2))

    def _parse(self, status: int, headers: Mapping[str, str], body: bytes) -> MicrolinkResponse:
        headers = {k.lower(): v for k, v in headers.items()}
        try:
            payload = json.loads(body.decode("utf-8")) if body else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as err:
            raise MicrolinkError(f"response is not JSON (HTTP {status})", http_status=status) from err

        rate = RateLimit.from_headers(headers)
        state = payload.get("status")
        if status == 429 or payload.get("code") == "ERATE":
            raise RateLimitError(
                payload.get("message") or "rate limit reached",
                code=payload.get("code", "ERATE"),
                http_status=status,
                more=payload.get("more"),
                details=payload.get("data"),
                reset=rate.reset,
            )
        if state != "success" or status >= 400:
            data = payload.get("data")
            detail = None
            if isinstance(data, dict) and data:
                detail = "; ".join(str(v) for v in data.values())
            raise MicrolinkError(
                detail or payload.get("message") or f"HTTP {status}",
                code=payload.get("code"),
                http_status=status,
                more=payload.get("more"),
                details=data,
            )
        return MicrolinkResponse(
            status=state,
            data=payload.get("data") or {},
            status_code=payload.get("statusCode"),
            rate_limit=rate,
            cache_status=headers.get("x-cache-status"),
            raw=payload,
        )

    # Helpers -----------------------------------------------------------

    def metadata(self, url: str, **options: Any) -> MicrolinkResponse:
        """Title, description, image, logo, author, date, publisher and language."""
        return self.get(url, **options)

    def screenshot(
        self,
        url: str,
        *,
        full_page: bool = False,
        type: str = "png",
        device: Optional[str] = None,
        element: Optional[str] = None,
        meta: bool = False,
        **options: Any,
    ) -> MicrolinkResponse:
        shot: Dict[str, Any] = {"type": type}
        if full_page:
            shot["fullPage"] = True
        if element:
            shot["element"] = element
        return self.get(url, screenshot=shot, device=device, meta=meta, **options)

    def pdf(self, url: str, *, format: str = "A4", meta: bool = False, **options: Any) -> MicrolinkResponse:
        return self.get(url, pdf={"format": format}, meta=meta, **options)

    def extract(self, url: str, rules: Mapping[str, Mapping[str, Any]], *, meta: bool = False, **options: Any) -> Dict[str, Any]:
        """Pull fields with CSS selector rules, for example
        ``{"heading": {"selector": "h1", "attr": "text"}}``. Returns ``data`` only.
        """
        if not rules:
            raise ValueError("rules must not be empty")
        resp = self.get(url, data=dict(rules), meta=meta, **options)
        return {k: resp.data.get(k) for k in rules}

    def palette(self, url: str, **options: Any) -> MicrolinkResponse:
        return self.get(url, palette=True, **options)

    def technologies(self, url: str, **options: Any) -> MicrolinkResponse:
        return self.get(url, insights={"technologies": True, "lighthouse": False}, meta=False, **options)
