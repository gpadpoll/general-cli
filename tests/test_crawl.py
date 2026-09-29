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


class TestDeepCommand:
    def test_deep_wires_bfs_strategy_and_returns_results(
        self, crawl_config_path, monkeypatch
    ):
        calls = _scripted_crawl_request(
            monkeypatch,
            {
                ("POST", "/crawl"): {
                    "success": True,
                    "results": [
                        {
                            "url": "https://example.com",
                            "success": True,
                            "markdown": "# hi",
                        }
                    ],
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
        crawler_config = calls[0]["json_body"]["crawler_config"]
        strategy = crawler_config["params"]["deep_crawl_strategy"]
        assert strategy["params"]["max_pages"] == 5

    def test_deep_with_create_evidence_uploads_each_successful_page(
        self, crawl_config_path, monkeypatch, tmp_path
    ):
        crawl_config_path.write_text(
            json.dumps({"crawl_cache_dir": str(tmp_path)})
        )
        _scripted_crawl_request(
            monkeypatch,
            {
                ("POST", "/crawl"): {
                    "success": True,
                    "results": [
                        {
                            "url": "https://example.com/a",
                            "success": True,
                            "markdown": "a",
                        },
                        {
                            "url": "https://example.com/b",
                            "success": False,
                            "markdown": None,
                        },
                    ],
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
        assert "evidence" not in payload["results"][1]
        assert len(kb_calls) == 1


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
