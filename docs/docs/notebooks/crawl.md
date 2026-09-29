# `crawl`: client for a local crawl4ai server

`gencli crawl` wraps a locally-run crawl4ai HTTP server
(`docker run unclecode/crawl4ai`), so a local agent can scrape web pages
and turn them into KB evidence — without ever calling crawl4ai or the KB
HTTP API directly itself.

crawl4ai's free/local library has **no general web search** — only
crawling of URLs you already know (a single page via `fetch`, or a
deep-crawl of a known domain via `deep`). The agent brings its own URLs.

As with `kb.ipynb`, this notebook demonstrates `gencli.crawl_client`'s
**pure** functions directly — no live network calls at doc-build time.
Each section notes the equivalent CLI command. A final, non-executed
section walks through a real session against a local crawl4ai + kb pair.


```python
%load_ext autoreload
%autoreload 2
```

## Fetching one page: `build_md_payload`

A pure function: given a URL and a markdown mode, always builds the same
request body for crawl4ai's `POST /md` endpoint — the simplest way to get
clean markdown for a single page.


```python
from gencli.crawl_client import build_md_payload

build_md_payload("https://example.com", mode="fit")
```

Equivalent CLI command:

```bash
gencli crawl fetch https://example.com --mode fit
```

## Config envelopes

crawl4ai's HTTP API wraps non-primitive config values (browser settings,
crawl behavior, deep-crawl strategies) as `{"type": ClassName, "params":
{...}}`. `build_config_envelope` builds that shape once; the more specific
builders below use it.


```python
from gencli.crawl_client import build_browser_config, build_bfs_deep_crawl_strategy

print(build_browser_config(headless=True))
build_bfs_deep_crawl_strategy(max_depth=2, max_pages=20, include_external=False)
```

Equivalent CLI command (deep-crawling a domain, BFS, up to 20 pages):

```bash
gencli crawl deep https://example.com --max-depth 2 --max-pages 20 --stay-on-domain
```

## Recognizing a crawl failure

crawl4ai can return **200 OK** with `{"success": false, ...}` when the
page itself failed to fetch or render — distinct from an HTTP error (a
non-2xx response, raised as `Crawl4AIError` instead). `is_crawl_failure`/
`extract_crawl_error` recognize this shape.


```python
from gencli.crawl_client import is_crawl_failure, extract_crawl_error

failed_body = {"success": False, "error": "net::ERR_NAME_NOT_RESOLVED"}

print(is_crawl_failure(failed_body))
print(is_crawl_failure({"success": True, "markdown": "# hi"}))
extract_crawl_error(failed_body)
```

`gencli crawl fetch`/`deep`/`screenshot` exit with code **2** (not the
generic 1) on exactly this shape:

```bash
gencli crawl fetch https://a-domain-that-does-not-exist.invalid
echo $?  # 2
```

## Pulling content out of a response

`extract_markdown` reads a `/md` response; `extract_first_result` reads
the first item out of a `/crawl` response's `results` list (raising
`ValueError` if it's empty, rather than returning `None` silently).


```python
from gencli.crawl_client import extract_markdown, extract_first_result

print(extract_markdown({"markdown": "# Example Domain", "success": True}))

crawl_body = {"results": [{"url": "https://example.com", "success": True, "markdown": "# hi"}]}
extract_first_result(crawl_body)
```

## Naming cached/uploaded artifacts: `build_artifact_filename`

A pure function: given a URL and an externally-minted id (per AGENTS.md's
rule to pass randomness in as a parameter rather than generate it
internally), builds a deterministic, readable filename.


```python
from gencli.crawl_client import build_artifact_filename

build_artifact_filename("https://example.com/products/widget", "a1b2c3", ".md")
```

## Talking to a real crawl4ai + kb instance

Not executed here (this is a docs build, not a live environment):

```bash
# One-time setup: run crawl4ai locally, and point gencli at it + the KB.
docker run -d -p 11235:11235 -e CRAWL4AI_API_TOKEN=devtoken unclecode/crawl4ai:latest
gencli config set crawl_base_url http://localhost:11235
gencli config set crawl_api_token devtoken
gencli config set kb_base_url http://localhost:8000
gencli config set kb_auth_mode none

# Connectivity checks.
gencli crawl health
gencli kb me

# Crawl a page and create KB evidence for it in one call.
gencli crawl fetch https://example.com --create-evidence
# -> {"url": "...", "markdown": "...", "evidence": {"id": "<uuid>", ...}}

# The agent reads the markdown above, decides what facts are in it, and
# ingests them using the *already-built* kb commands -- gencli crawl never
# does the fact extraction itself:
gencli kb attributes create page_title --value-type string
gencli kb facts ingest example-com page_title --value-type string \
    --value "Example Domain" --evidence-id <id-from-above>
```

See `gencli/commands/crawl.py`'s module docstring for the full exit-code
scheme, and the project README for `deep`/`screenshot` examples.
