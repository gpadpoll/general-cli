"""
Client command group for a local crawl4ai HTTP server.

This module contains *only* thin Typer command wrappers: parse arguments,
call one `gencli.crawl_client` (and, for `--create-evidence`,
`gencli.kb_client`) function, render the result, and translate errors into
exit codes. All real logic lives in `gencli/crawl_client.py` and is
unit-tested there — see AGENTS.md.

crawl4ai's free/local library has no general web search — only crawling of
URLs you already know (a single page via `fetch`, or a deep-crawl of a
known domain via `deep`). The agent brings its own URLs.

Output is JSON by default on every command (this CLI's primary audience is
agents).

Exit codes:
    0  success
    1  a generic crawl4ai/KB HTTP error, or a local-argument error
    2  crawl4ai returned 200 with {"success": false, ...} — the page
       itself failed to fetch/render, distinct from a transport/HTTP error
    3  crawl4ai or the KB couldn't be reached at all, or local config
       itself is misconfigured

Configure with `gencli config set crawl_base_url ...` / `crawl_api_token` /
`crawl_cache_dir`. `--create-evidence` also uses the `kb_*` config keys
(see `gencli/commands/kb.py`) — evidence always goes to the KB regardless
of which crawl4ai server was used.
"""

import json
import sys
import uuid
from enum import Enum
from typing import Any, Callable, Dict, NoReturn, Optional, Tuple, TypeVar

import google.auth.exceptions
import httpx
import typer

from gencli import crawl_client as cc
from gencli import kb_client as kc
from gencli.commands.config import DEFAULT_CONFIG, load_config

app = typer.Typer(
    no_args_is_help=True,
    help="Crawl web pages via a local crawl4ai server.",
)

T = TypeVar("T")

CREATE_EVIDENCE_OPTION = typer.Option(
    False,
    "--create-evidence",
    help="Upload the crawled content as KB evidence, included in the output.",
)


class MarkdownMode(str, Enum):
    fit = "fit"
    raw = "raw"
    llm = "llm"


# --------------------------------------------------------------------------
# Shared plumbing: config -> server contexts, error -> exit code.
# --------------------------------------------------------------------------


def _fail(payload: Dict[str, Any], exit_code: int) -> NoReturn:
    # Plain print, not a Rich console: JSON output must always be clean and
    # machine-parseable, never carrying ANSI styling.
    print(json.dumps(payload, default=str), file=sys.stderr)
    raise typer.Exit(exit_code)


def _fail_value_error(exc: Exception) -> NoReturn:
    _fail({"error": str(exc), "status_code": None}, 1)


def _fail_config_error(exc: Exception) -> NoReturn:
    _fail({"error": str(exc), "status_code": None}, 3)


def _fail_connection(exc: Exception) -> NoReturn:
    _fail({"error": str(exc), "status_code": None}, 3)


def _fail_crawl_api(exc: cc.Crawl4AIError) -> NoReturn:
    message = (
        cc.extract_crawl_error(exc.body)
        if isinstance(exc.body, dict)
        else str(exc.body)
    )
    _fail({"error": message, "status_code": exc.status_code}, 1)


def _fail_crawl_semantic_failure(body: Any) -> NoReturn:
    _fail({"error": cc.extract_crawl_error(body), "status_code": 200}, 2)


def _fail_kb_api(exc: kc.KBAPIError) -> NoReturn:
    _fail(
        {
            "error": kc.extract_error_message(exc.status_code, exc.body),
            "status_code": exc.status_code,
        },
        1,
    )


def _call_crawl(fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Call a crawl_client I/O function, translating errors to exit codes."""
    try:
        return fn(*args, **kwargs)
    except cc.Crawl4AIError as exc:
        _fail_crawl_api(exc)
    except httpx.RequestError as exc:
        _fail_connection(exc)


def _crawl_context() -> Tuple[str, Dict[str, str]]:
    """Load config and build (base_url, headers) for the crawl4ai server."""
    cfg = load_config()
    base_url = str(
        cfg.get("crawl_base_url") or DEFAULT_CONFIG["crawl_base_url"]
    )
    headers = cc.build_auth_header(cfg.get("crawl_api_token"))
    return base_url, headers


def _kb_context() -> Tuple[str, Dict[str, str]]:
    """
    Same logic as `gencli.commands.kb`'s `_client_context()`: evidence
    always goes to the KB regardless of which crawl4ai server was used, so
    this reads the `kb_*` config keys, not `crawl_*`.
    """
    cfg = load_config()
    base_url = str(cfg.get("kb_base_url") or DEFAULT_CONFIG["kb_base_url"])
    try:
        headers = kc.build_headers_for_config(cfg)
    except ValueError as exc:
        _fail_config_error(exc)
    except google.auth.exceptions.GoogleAuthError as exc:
        _fail_config_error(exc)
    return base_url, headers


def _print_json(data: Any) -> None:
    print(json.dumps(data, default=str))


def _maybe_create_evidence(
    source_url: str, content: bytes, filename: str, media_type: str
) -> Dict[str, Any]:
    """Cache `content` locally, then upload it as KB evidence for the url."""
    cfg = load_config()
    cache_dir = str(
        cfg.get("crawl_cache_dir") or DEFAULT_CONFIG["crawl_cache_dir"]
    )
    cached_path = cc.save_bytes_to_cache(cache_dir, filename, content)
    base_url, headers = _kb_context()
    try:
        return kc.upload_evidence(
            base_url,
            headers,
            source_url,
            cached_path,
            media_type=media_type,
            filename=filename,
        )
    except kc.KBAPIError as exc:
        _fail_kb_api(exc)
    except httpx.RequestError as exc:
        _fail_connection(exc)


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


@app.command()
def fetch(
    url: str = typer.Argument(..., help="URL to fetch clean markdown for."),
    mode: MarkdownMode = typer.Option(
        MarkdownMode.fit, "--mode", help="crawl4ai markdown mode."
    ),
    query: Optional[str] = typer.Option(
        None, "--query", help="Query for --mode llm's content filter."
    ),
    create_evidence: bool = CREATE_EVIDENCE_OPTION,
    media_type: str = typer.Option(
        "text/markdown", help="MIME type recorded on the created evidence."
    ),
) -> None:
    """Fetch a single URL's clean markdown via crawl4ai's /md endpoint."""
    base_url, headers = _crawl_context()
    body = _call_crawl(
        cc.fetch_markdown, base_url, headers, url, mode.value, query
    )
    if cc.is_crawl_failure(body):
        _fail_crawl_semantic_failure(body)
    markdown = cc.extract_markdown(body)
    result: Dict[str, Any] = {"url": url, "markdown": markdown}
    if create_evidence:
        filename = cc.build_artifact_filename(url, uuid.uuid4().hex, ".md")
        result["evidence"] = _maybe_create_evidence(
            url, markdown.encode("utf-8"), filename, media_type
        )
    _print_json(result)


@app.command()
def deep(
    url: str = typer.Argument(..., help="Root URL to deep-crawl."),
    max_depth: Optional[int] = typer.Option(
        2, help="Maximum link depth from the root URL."
    ),
    max_pages: Optional[int] = typer.Option(
        20, help="Maximum pages to crawl."
    ),
    stay_on_domain: bool = typer.Option(
        True,
        "--stay-on-domain/--allow-external",
        help="Restrict the crawl to the root URL's domain.",
    ),
    create_evidence: bool = CREATE_EVIDENCE_OPTION,
    media_type: str = typer.Option(
        "text/markdown", help="MIME type recorded on created evidence."
    ),
) -> None:
    """Deep-crawl a domain (BFS) from url, via crawl4ai's /crawl endpoint."""
    base_url, headers = _crawl_context()
    strategy = cc.build_bfs_deep_crawl_strategy(
        max_depth=max_depth,
        max_pages=max_pages,
        include_external=not stay_on_domain,
    )
    crawler_config = cc.build_crawler_run_config(deep_crawl_strategy=strategy)
    body = _call_crawl(
        cc.crawl, base_url, headers, [url], crawler_config=crawler_config
    )
    if cc.is_crawl_failure(body):
        _fail_crawl_semantic_failure(body)

    output = []
    for item in body.get("results", []):
        page_url = item.get("url", url)
        entry: Dict[str, Any] = {
            "url": page_url,
            "success": item.get("success"),
            "markdown": item.get("markdown"),
        }
        if create_evidence and item.get("success"):
            markdown = item.get("markdown") or ""
            filename = cc.build_artifact_filename(
                page_url, uuid.uuid4().hex, ".md"
            )
            entry["evidence"] = _maybe_create_evidence(
                page_url, markdown.encode("utf-8"), filename, media_type
            )
        output.append(entry)
    _print_json({"url": url, "results": output})


@app.command()
def screenshot(
    url: str = typer.Argument(..., help="URL to screenshot."),
    wait_for: Optional[float] = typer.Option(
        None, "--wait-for", help="Seconds to wait before capturing."
    ),
    create_evidence: bool = CREATE_EVIDENCE_OPTION,
    media_type: str = typer.Option(
        "image/png", help="MIME type recorded on the created evidence."
    ),
) -> None:
    """Capture a full-page PNG screenshot via crawl4ai's /screenshot
    endpoint."""
    base_url, headers = _crawl_context()
    body = _call_crawl(cc.screenshot, base_url, headers, url, wait_for)
    if cc.is_crawl_failure(body):
        _fail_crawl_semantic_failure(body)
    image_bytes = cc.decode_screenshot(body)
    result: Dict[str, Any] = {"url": url, "bytes": len(image_bytes)}
    if create_evidence:
        filename = cc.build_artifact_filename(url, uuid.uuid4().hex, ".png")
        result["evidence"] = _maybe_create_evidence(
            url, image_bytes, filename, media_type
        )
    else:
        cfg = load_config()
        cache_dir = str(
            cfg.get("crawl_cache_dir") or DEFAULT_CONFIG["crawl_cache_dir"]
        )
        filename = cc.build_artifact_filename(url, uuid.uuid4().hex, ".png")
        result["cached_path"] = cc.save_bytes_to_cache(
            cache_dir, filename, image_bytes
        )
    _print_json(result)


@app.command()
def health() -> None:
    """Check connectivity to the configured crawl4ai server."""
    cfg = load_config()
    base_url = str(
        cfg.get("crawl_base_url") or DEFAULT_CONFIG["crawl_base_url"]
    )
    data = _call_crawl(cc.health, base_url)
    _print_json(data)
