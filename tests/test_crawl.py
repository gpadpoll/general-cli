"""
CLI-wiring tests for `gencli crawl`.

These monkeypatch `gencli.crawl_client.request` (and, for `--create-evidence`,
`gencli.kb_client.request`) — the chokepoints every I/O function funnels
through — so no real network call is ever made. They check argument
parsing, exit codes, and basic output shape; the underlying logic is
already covered directly in tests/test_crawl_client.py.
"""

import json
import os
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from gencli import crawl_client, kb_client
from gencli.main import app

runner = CliRunner()


def _scripted_crawl_request(monkeypatch, responses):
    """
    Patch gencli.crawl_client.request with a fake that looks up its return
    value (or raises, if the value is an Exception) from `responses`, keyed
    by (method, path). Returns the list of captured calls.
    """
    calls = []

    def fake(
        method,
        path,
        *,
        base_url,
        headers=None,
        params=None,
        json_body=None,
        timeout=30.0,
    ):
        calls.append({"method": method, "path": path, "json_body": json_body})
        key = (method, path)
        if key not in responses:
            raise AssertionError(f"unexpected request: {key}")
        result = responses[key]
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(crawl_client, "request", fake)
    return calls


def _scripted_kb_request(monkeypatch, responses):
    calls = []

    def fake(
        method,
        path,
        *,
        base_url,
        headers=None,
        params=None,
        json_body=None,
        data=None,
        files=None,
        timeout=30.0,
    ):
        calls.append({"method": method, "path": path, "data": data})
        key = (method, path)
        if key not in responses:
            raise AssertionError(f"unexpected kb request: {key}")
        result = responses[key]
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(kb_client, "request", fake)
    return calls


@pytest.fixture
def crawl_config_path(tmp_path):
    """An isolated GENCLI_CONFIG_PATH per test."""
    config_path = tmp_path / "config.json"
    with patch.dict(os.environ, {"GENCLI_CONFIG_PATH": str(config_path)}):
        yield config_path


def test_crawl_help(crawl_config_path):
    result = runner.invoke(app, ["crawl", "--help"])
    assert result.exit_code == 0
    assert "crawl4ai" in result.output


class TestFetchCommand:
    def test_fetch_default_json(self, crawl_config_path, monkeypatch):
        _scripted_crawl_request(
            monkeypatch,
            {("POST", "/md"): {"markdown": "# hi", "success": True}},
        )
        result = runner.invoke(app, ["crawl", "fetch", "https://example.com"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload == {"url": "https://example.com", "markdown": "# hi"}

    def test_fetch_with_create_evidence(
        self, crawl_config_path, monkeypatch, tmp_path
    ):
        crawl_config_path.write_text(
            json.dumps({"crawl_cache_dir": str(tmp_path)})
        )
        _scripted_crawl_request(
            monkeypatch,
            {("POST", "/md"): {"markdown": "# hi", "success": True}},
        )
        kb_calls = _scripted_kb_request(
            monkeypatch,
            {
                ("POST", "/api/evidence/upload"): {
                    "id": "ev1",
                    "url": "https://example.com",
                }
            },
        )
        result = runner.invoke(
            app, ["crawl", "fetch", "https://example.com", "--create-evidence"]
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["markdown"] == "# hi"
        assert payload["evidence"] == {
            "id": "ev1",
            "url": "https://example.com",
        }
        assert len(kb_calls) == 1
        assert kb_calls[0]["path"] == "/api/evidence/upload"

    def test_fetch_crawl_failure_exits_2(self, crawl_config_path, monkeypatch):
        _scripted_crawl_request(
            monkeypatch,
            {("POST", "/md"): {"success": False, "error": "timeout"}},
        )
        result = runner.invoke(app, ["crawl", "fetch", "https://example.com"])
        assert result.exit_code == 2
        payload = json.loads(result.output)
        assert payload == {"error": "timeout", "status_code": 200}

    def test_fetch_connection_error_exits_3(
        self, crawl_config_path, monkeypatch
    ):
        import httpx

        def fake_request(*args, **kwargs):
            raise httpx.ConnectError("connection refused")

        monkeypatch.setattr(crawl_client, "request", fake_request)
        result = runner.invoke(app, ["crawl", "fetch", "https://example.com"])
        assert result.exit_code == 3

    def test_fetch_http_error_exits_1(self, crawl_config_path, monkeypatch):
        _scripted_crawl_request(
            monkeypatch,
            {
                ("POST", "/md"): crawl_client.Crawl4AIError(
                    502, {"detail": "bad gateway"}, "http://x"
                )
            },
        )
        result = runner.invoke(app, ["crawl", "fetch", "https://example.com"])
        assert result.exit_code == 1
        payload = json.loads(result.output)
        assert payload == {"error": "bad gateway", "status_code": 502}


def _fake_crawl_by_url(monkeypatch, pages):
    """
    Patch crawl_client.request so each simulated `deep` page-crawl (one
    /crawl call per page, batched as urls=[<that page>]) returns the
    canned result for that page from `pages` (keyed by URL). Returns the
    list of crawled URLs, in call order.
    """
    crawled = []

    def fake(
        method,
        path,
        *,
        base_url,
        headers=None,
        params=None,
        json_body=None,
        timeout=30.0,
    ):
        assert (method, path) == ("POST", "/crawl")
        page_url = json_body["urls"][0]
        crawled.append(page_url)
        if page_url not in pages:
            raise AssertionError(f"unexpected page crawled: {page_url}")
        return {"success": True, "results": [pages[page_url]]}

    monkeypatch.setattr(crawl_client, "request", fake)
    return crawled


class TestDeepCommand:
    def test_deep_crawls_single_page_with_no_links(
        self, crawl_config_path, monkeypatch
    ):
        crawled = _fake_crawl_by_url(
            monkeypatch,
            {
                "https://example.com": {
                    "url": "https://example.com",
                    "success": True,
                    "markdown": "# hi",
                    "links": {"internal": [], "external": []},
                }
            },
        )
        result = runner.invoke(
            app, ["crawl", "deep", "https://example.com", "--max-pages", "5"]
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["results"] == [
            {"url": "https://example.com", "success": True, "markdown": "# hi"}
        ]
        assert crawled == ["https://example.com"]

    def test_deep_follows_internal_links_breadth_first(
        self, crawl_config_path, monkeypatch
    ):
        crawled = _fake_crawl_by_url(
            monkeypatch,
            {
                "https://example.com": {
                    "url": "https://example.com",
                    "success": True,
                    "markdown": "root",
                    "links": {
                        "internal": [{"href": "https://example.com/a"}],
                        "external": [{"href": "https://other.com/x"}],
                    },
                },
                "https://example.com/a": {
                    "url": "https://example.com/a",
                    "success": True,
                    "markdown": "page a",
                    "links": {"internal": [], "external": []},
                },
            },
        )
        result = runner.invoke(
            app, ["crawl", "deep", "https://example.com", "--max-pages", "5"]
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert [r["url"] for r in payload["results"]] == [
            "https://example.com",
            "https://example.com/a",
        ]
        # external link never crawled: --stay-on-domain is the default
        assert "https://other.com/x" not in crawled

    def test_deep_stops_at_max_pages(self, crawl_config_path, monkeypatch):
        _fake_crawl_by_url(
            monkeypatch,
            {
                "https://example.com": {
                    "url": "https://example.com",
                    "success": True,
                    "markdown": "root",
                    "links": {
                        "internal": [
                            {"href": "https://example.com/a"},
                            {"href": "https://example.com/b"},
                        ],
                        "external": [],
                    },
                },
                "https://example.com/a": {
                    "url": "https://example.com/a",
                    "success": True,
                    "markdown": "a",
                    "links": {"internal": [], "external": []},
                },
            },
        )
        result = runner.invoke(
            app, ["crawl", "deep", "https://example.com", "--max-pages", "2"]
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert len(payload["results"]) == 2

    def test_deep_with_create_evidence_uploads_each_successful_page(
        self, crawl_config_path, monkeypatch, tmp_path
    ):
        crawl_config_path.write_text(
            json.dumps({"crawl_cache_dir": str(tmp_path)})
        )
        _fake_crawl_by_url(
            monkeypatch,
            {
                "https://example.com": {
                    "url": "https://example.com",
                    "success": True,
                    "markdown": "root",
                    "links": {"internal": [], "external": []},
                }
            },
        )
        kb_calls = _scripted_kb_request(
            monkeypatch, {("POST", "/api/evidence/upload"): {"id": "ev1"}}
        )
        result = runner.invoke(
            app, ["crawl", "deep", "https://example.com", "--create-evidence"]
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["results"][0]["evidence"] == {"id": "ev1"}
        assert len(kb_calls) == 1

    def test_deep_dict_markdown_is_normalized_to_plain_text(
        self, crawl_config_path, monkeypatch
    ):
        # /crawl can return markdown as a dict (fit_markdown/raw_markdown/...)
        # rather than a plain string like /md always uses.
        _fake_crawl_by_url(
            monkeypatch,
            {
                "https://example.com": {
                    "url": "https://example.com",
                    "success": True,
                    "markdown": {
                        "fit_markdown": "fit text",
                        "raw_markdown": "raw text",
                    },
                    "links": {"internal": [], "external": []},
                }
            },
        )
        result = runner.invoke(app, ["crawl", "deep", "https://example.com"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["results"][0]["markdown"] == "fit text"


class TestScreenshotCommand:
    def test_screenshot_without_create_evidence_caches_locally(
        self, crawl_config_path, monkeypatch, tmp_path
    ):
        crawl_config_path.write_text(
            json.dumps({"crawl_cache_dir": str(tmp_path)})
        )
        import base64

        encoded = base64.b64encode(b"\x89PNG").decode()
        _scripted_crawl_request(
            monkeypatch,
            {
                ("POST", "/screenshot"): {
                    "success": True,
                    "screenshot": encoded,
                }
            },
        )
        result = runner.invoke(
            app, ["crawl", "screenshot", "https://example.com"]
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["bytes"] == 4
        assert os.path.exists(payload["cached_path"])

    def test_screenshot_with_create_evidence(
        self, crawl_config_path, monkeypatch, tmp_path
    ):
        crawl_config_path.write_text(
            json.dumps({"crawl_cache_dir": str(tmp_path)})
        )
        import base64

        encoded = base64.b64encode(b"\x89PNG").decode()
        _scripted_crawl_request(
            monkeypatch,
            {
                ("POST", "/screenshot"): {
                    "success": True,
                    "screenshot": encoded,
                }
            },
        )
        _scripted_kb_request(
            monkeypatch, {("POST", "/api/evidence/upload"): {"id": "ev2"}}
        )
        result = runner.invoke(
            app,
            [
                "crawl",
                "screenshot",
                "https://example.com",
                "--create-evidence",
            ],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["evidence"] == {"id": "ev2"}
        assert "cached_path" not in payload


class TestHealthCommand:
    def test_health(self, crawl_config_path, monkeypatch):
        _scripted_crawl_request(
            monkeypatch, {("GET", "/health"): {"status": "ok"}}
        )
        result = runner.invoke(app, ["crawl", "health"])
        assert result.exit_code == 0
        assert json.loads(result.output) == {"status": "ok"}
