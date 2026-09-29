"""
Client library for a local crawl4ai HTTP server
(https://github.com/unclecode/crawl4ai).

crawl4ai's free/local library has no general web search — only crawling of
URLs you already know (a single page, or a deep-crawl of a known domain via
BFS/sitemap). This module talks to a locally-run `docker run
unclecode/crawl4ai` server over HTTP, the same way `gencli/kb_client.py`
talks to the Knowledge Base — keeping gencli itself dependency-light (just
`httpx`) rather than pulling in crawl4ai's own Playwright/browser stack.

Two layers, per AGENTS.md's functional-programming rules:

1. Pure functions: payload/config-envelope builders and response-shape
   helpers. No I/O, no side effects, deterministic for a given input.
2. I/O boundary functions: the single low-level HTTP call (`request`) every
   per-endpoint function funnels through, plus local cache-file writes.
"""

import base64
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

_SLUG_NON_ALNUM = re.compile(r"[^a-zA-Z0-9]+")


class Crawl4AIError(Exception):
    """Raised when the crawl4ai server returns a non-2xx response."""

    def __init__(self, status_code: int, body: Any, url: str) -> None:
        self.status_code = status_code
        self.body = body
        self.url = url
        super().__init__(f"{status_code} from {url}: {body!r}")


# --------------------------------------------------------------------------
# Pure functions (no I/O, no side effects, deterministic output for a given
# input). See AGENTS.md's functional programming rules.
# --------------------------------------------------------------------------


def build_auth_header(token: Optional[str]) -> Dict[str, str]:
    """Build an ``Authorization`` header dict, or ``{}`` if no token."""
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


def build_md_payload(
    url: str, mode: str = "fit", query: Optional[str] = None
) -> Dict[str, Any]:
    """Build the body for ``POST /md`` (clean markdown for one URL)."""
    payload: Dict[str, Any] = {"url": url, "f": mode}
    if query is not None:
        payload["q"] = query
    return payload


def build_config_envelope(class_name: str, **params: Any) -> Dict[str, Any]:
    """
    Build crawl4ai's generic ``{"type": class_name, "params": {...}}``
    wrapper required for non-primitive config values (``BrowserConfig``,
    ``CrawlerRunConfig``, deep-crawl strategies, ...) sent over its REST
    API. ``None``-valued kwargs are omitted, so callers can pass every
    optional parameter unconditionally.
    """
    return {
        "type": class_name,
        "params": {k: v for k, v in params.items() if v is not None},
    }


def build_browser_config(**kwargs: Any) -> Dict[str, Any]:
    """Build a ``BrowserConfig`` envelope (headless, viewport, proxy, ...)."""
    return build_config_envelope("BrowserConfig", **kwargs)


def build_bfs_deep_crawl_strategy(
    max_depth: Optional[int] = None,
    max_pages: Optional[int] = None,
    include_external: bool = False,
    **extra: Any,
) -> Dict[str, Any]:
    """
    Build a BFS deep-crawl strategy envelope. Only the well-documented
    fields are named explicitly; crawl4ai's HTTP API docs don't fully
    enumerate every field it accepts, so ``**extra`` passes anything else
    through unchanged.
    """
    return build_config_envelope(
        "BFSDeepCrawlStrategy",
        max_depth=max_depth,
        max_pages=max_pages,
        include_external=include_external,
        **extra,
    )


def build_crawler_run_config(
    deep_crawl_strategy: Optional[Dict[str, Any]] = None, **kwargs: Any
) -> Dict[str, Any]:
    """Build a ``CrawlerRunConfig`` envelope, optionally with a deep-crawl
    strategy."""
    params = {k: v for k, v in kwargs.items() if v is not None}
    if deep_crawl_strategy is not None:
        params["deep_crawl_strategy"] = deep_crawl_strategy
    return {"type": "CrawlerRunConfig", "params": params}


def build_crawl_payload(
    urls: List[str],
    browser_config: Optional[Dict[str, Any]] = None,
    crawler_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the body for ``POST /crawl`` (single or batch/deep crawl)."""
    payload: Dict[str, Any] = {"urls": urls}
    if browser_config is not None:
        payload["browser_config"] = browser_config
    if crawler_config is not None:
        payload["crawler_config"] = crawler_config
    return payload


def build_screenshot_payload(
    url: str, wait_for: Optional[float] = None
) -> Dict[str, Any]:
    """Build the body for ``POST /screenshot``."""
    payload: Dict[str, Any] = {"url": url}
    if wait_for is not None:
        payload["screenshot_wait_for"] = wait_for
    return payload


def is_crawl_failure(body: Any) -> bool:
    """
    True iff crawl4ai returned 200 with ``{"success": false, ...}`` — the
    page itself failed to fetch/render. Distinct from an HTTP error, which
    surfaces as ``Crawl4AIError`` instead.
    """
    return isinstance(body, dict) and body.get("success") is False


def extract_crawl_error(body: Any) -> str:
    """A human-readable message for a crawl4ai semantic failure body."""
    if isinstance(body, dict):
        for key in ("error", "error_message", "detail"):
            if isinstance(body.get(key), str):
                return body[key]
    return str(body)


def extract_markdown(body: Dict[str, Any]) -> str:
    """Pull the markdown string out of a ``POST /md`` response body."""
    markdown: str = body["markdown"]
    return markdown


def extract_first_result(body: Dict[str, Any]) -> Dict[str, Any]:
    """
    Pull the first item out of a ``POST /crawl`` response's ``results``
    list.

    Raises:
        ValueError: if ``results`` is missing or empty.
    """
    results = body.get("results") or []
    if not results:
        raise ValueError("crawl response contained no results")
    first: Dict[str, Any] = results[0]
    return first


def decode_screenshot(body: Dict[str, Any]) -> bytes:
    """Decode a ``POST /screenshot`` response's base64 PNG into raw bytes."""
    return base64.b64decode(body["screenshot"])


def build_artifact_filename(url: str, artifact_id: str, suffix: str) -> str:
    """
    Build a deterministic, readable cache/upload filename from ``url``,
    disambiguated by an externally-minted ``artifact_id`` (per AGENTS.md's
    rule to pass randomness in as a parameter rather than generate it
    internally, so this stays a pure, testable function).
    """
    parsed = urlparse(url)
    slug = (
        _SLUG_NON_ALNUM.sub("-", f"{parsed.netloc}{parsed.path}")
        .strip("-")
        .lower()
        or "page"
    )
    return f"{slug}-{artifact_id}{suffix}"


# --------------------------------------------------------------------------
# I/O boundary: the single low-level HTTP call, and local cache-file writes.
# Everything above this line is pure; everything below performs real I/O.
# --------------------------------------------------------------------------


def request(
    method: str,
    path: str,
    *,
    base_url: str,
    headers: Optional[Dict[str, str]] = None,
    params: Optional[Dict[str, Any]] = None,
    json_body: Optional[Dict[str, Any]] = None,
    timeout: float = 30.0,
) -> Any:
    """
    The single low-level HTTP call every function in this module funnels
    through — the one function CLI-wiring tests monkeypatch.

    Returns the parsed JSON response body (or raw text if the response
    isn't JSON).

    Raises:
        Crawl4AIError: on a non-2xx response.
        httpx.RequestError: on a transport-level failure (DNS, connection,
            timeout) — there was no HTTP response at all.
    """
    response = httpx.request(
        method,
        f"{base_url.rstrip('/')}{path}",
        headers=headers,
        params=params,
        json=json_body,
        timeout=timeout,
    )
    try:
        body = response.json()
    except ValueError:
        body = response.text
    if response.is_error:
        raise Crawl4AIError(response.status_code, body, str(response.url))
    return body


def health(base_url: str, timeout: float = 5.0) -> Any:
    """``GET /health`` — no auth header needed, per crawl4ai's own API."""
    return request("GET", "/health", base_url=base_url, timeout=timeout)


def fetch_markdown(
    base_url: str,
    headers: Dict[str, str],
    url: str,
    mode: str = "fit",
    query: Optional[str] = None,
    timeout: float = 60.0,
) -> Dict[str, Any]:
    payload = build_md_payload(url, mode, query)
    return request(
        "POST",
        "/md",
        base_url=base_url,
        headers=headers,
        json_body=payload,
        timeout=timeout,
    )


def crawl(
    base_url: str,
    headers: Dict[str, str],
    urls: List[str],
    browser_config: Optional[Dict[str, Any]] = None,
    crawler_config: Optional[Dict[str, Any]] = None,
    timeout: float = 120.0,
) -> Dict[str, Any]:
    payload = build_crawl_payload(urls, browser_config, crawler_config)
    return request(
        "POST",
        "/crawl",
        base_url=base_url,
        headers=headers,
        json_body=payload,
        timeout=timeout,
    )


def screenshot(
    base_url: str,
    headers: Dict[str, str],
    url: str,
    wait_for: Optional[float] = None,
    timeout: float = 60.0,
) -> Dict[str, Any]:
    payload = build_screenshot_payload(url, wait_for)
    return request(
        "POST",
        "/screenshot",
        base_url=base_url,
        headers=headers,
        json_body=payload,
        timeout=timeout,
    )


def save_bytes_to_cache(cache_dir: str, filename: str, data: bytes) -> str:
    """Write ``data`` under ``cache_dir/filename``, creating dirs as needed."""
    path = Path(cache_dir).expanduser() / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return str(path)


def save_text_to_cache(cache_dir: str, filename: str, text: str) -> str:
    """Write ``text`` (UTF-8) under ``cache_dir/filename``."""
    return save_bytes_to_cache(cache_dir, filename, text.encode("utf-8"))
