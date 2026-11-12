# microlink-python

A small typed Python client for the [Microlink API](https://microlink.io/docs/api/getting-started/overview): link metadata, screenshots, PDFs, color palettes, technology detection and CSS-selector data extraction.

It uses only the standard library, works on the free plan without a key, and switches to `pro.microlink.io` with the `x-api-key` header when you pass one.

## Install

```bash
pip install git+https://github.com/SanderSaxBuilds/microlink-python
```

Python 3.9 or newer.

## Usage

```python
from microlink import Microlink

ml = Microlink()  # free plan, or Microlink(api_key="...") for Pro

page = ml.metadata("https://github.com/microlinkhq")
print(page.data["title"], page.data["image"]["url"])
print(page.rate_limit.remaining, "calls left")

shot = ml.screenshot("https://example.com", full_page=True)
print(shot.asset_url("screenshot"))

pdf = ml.pdf("https://example.com", format="Letter")
print(pdf.asset_url("pdf"))

fields = ml.extract(
    "https://news.ycombinator.com",
    {"top": {"selector": ".titleline > a", "attr": "text"}},
)
print(fields["top"])
```

Any Microlink parameter can be passed to `get()` as a keyword argument. Nested dicts become the dotted keys the API expects:

```python
ml.get("https://example.com", screenshot={"fullPage": True, "type": "jpeg"}, waitForTimeout=1500, meta=False)
# -> ?url=...&screenshot=true&screenshot.fullPage=true&screenshot.type=jpeg&waitForTimeout=1500&meta=false
```

## Errors and retries

- `MicrolinkError` carries the API's `code` (for example `EINVALURL`), the HTTP status, and the `more` help link.
- `RateLimitError` is raised on HTTP 429 or `ERATE`, with `reset` as a Unix time. It is not retried, since the free quota resets daily.
- Network errors and 5xx responses are retried with exponential backoff (`retries=2` by default).
- Every response carries `rate_limit` (limit, remaining, reset) and `cache_status` from the response headers.

## Tests

```bash
pip install pytest
pytest                      # 15 tests on responses recorded from api.microlink.io
MICROLINK_LIVE=1 pytest     # adds 1 call to the live API
```

The recorded responses are in `tests/fixtures`. The client takes a `transport` callable, so tests replay them without the network.

## License

MIT
