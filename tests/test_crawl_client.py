"""
Test suite for gencli.crawl_client.

Pure functions are tested directly with plain asserts. I/O functions are
tested by monkeypatching `crawl_client.request` — the single chokepoint
every other I/O function in the module funnels through — so no real
network call is ever made.
"""

from pathlib import Path

import pytest

from gencli import crawl_client

# --------------------------------------------------------------------------
# Pure functions
# --------------------------------------------------------------------------


class TestBuildAuthHeader:
    def test_with_token(self):
        assert crawl_client.build_auth_header("abc") == {
            "Authorization": "Bearer abc"
        }

    def test_without_token(self):
        assert crawl_client.build_auth_header(None) == {}
        assert crawl_client.build_auth_header("") == {}


class TestBuildMdPayload:
    def test_default_mode(self):
        assert crawl_client.build_md_payload("https://example.com") == {
            "url": "https://example.com",
            "f": "fit",
        }

    def test_with_query(self):
        payload = crawl_client.build_md_payload(
            "https://example.com", mode="llm", query="pricing"
        )
        assert payload == {
            "url": "https://example.com",
            "f": "llm",
            "q": "pricing",
        }


class TestBuildConfigEnvelope:
    def test_wraps_type_and_params(self):
        envelope = crawl_client.build_config_envelope(
            "BrowserConfig", headless=True
        )
        assert envelope == {
            "type": "BrowserConfig",
            "params": {"headless": True},
        }

    def test_omits_none_valued_kwargs(self):
        envelope = crawl_client.build_config_envelope(
            "BrowserConfig", headless=True, proxy=None
        )
        assert envelope == {
            "type": "BrowserConfig",
            "params": {"headless": True},
        }


class TestBuildBrowserConfig:
    def test_delegates_to_config_envelope(self):
        assert crawl_client.build_browser_config(headless=True) == {
            "type": "BrowserConfig",
            "params": {"headless": True},
        }


class TestBuildBfsDeepCrawlStrategy:
    def test_named_fields(self):
        strategy = crawl_client.build_bfs_deep_crawl_strategy(
            max_depth=2, max_pages=10
        )
        assert strategy == {
            "type": "BFSDeepCrawlStrategy",
            "params": {
                "max_depth": 2,
                "max_pages": 10,
                "include_external": False,
            },
        }

    def test_extra_fields_pass_through(self):
        strategy = crawl_client.build_bfs_deep_crawl_strategy(
            max_pages=5, score_threshold=0.5
        )
        assert strategy["params"]["score_threshold"] == 0.5


class TestBuildCrawlerRunConfig:
    def test_without_deep_crawl_strategy(self):
        config = crawl_client.build_crawler_run_config(stream=False)
        assert config == {
            "type": "CrawlerRunConfig",
            "params": {"stream": False},
        }

    def test_with_deep_crawl_strategy(self):
        strategy = crawl_client.build_bfs_deep_crawl_strategy(max_pages=5)
        config = crawl_client.build_crawler_run_config(
            deep_crawl_strategy=strategy
        )
        assert config["params"]["deep_crawl_strategy"] == strategy


class TestBuildCrawlPayload:
    def test_minimal(self):
        assert crawl_client.build_crawl_payload(["https://x"]) == {
            "urls": ["https://x"]
        }

    def test_with_configs(self):
        browser_config = crawl_client.build_browser_config(headless=True)
        crawler_config = crawl_client.build_crawler_run_config(stream=False)
        payload = crawl_client.build_crawl_payload(
            ["https://x"],
            browser_config=browser_config,
            crawler_config=crawler_config,
        )
        assert payload == {
            "urls": ["https://x"],
            "browser_config": browser_config,
            "crawler_config": crawler_config,
        }


class TestBuildScreenshotPayload:
    def test_without_wait_for(self):
        assert crawl_client.build_screenshot_payload("https://x") == {
            "url": "https://x"
        }

    def test_with_wait_for(self):
        payload = crawl_client.build_screenshot_payload(
            "https://x", wait_for=2.5
        )
        assert payload == {"url": "https://x", "screenshot_wait_for": 2.5}


class TestIsCrawlFailure:
    def test_true_on_explicit_false(self):
        assert (
            crawl_client.is_crawl_failure({"success": False, "error": "boom"})
            is True
        )

    def test_false_on_success(self):
        assert (
            crawl_client.is_crawl_failure({"success": True, "markdown": "x"})
            is False
        )

    def test_false_on_non_dict(self):
        assert crawl_client.is_crawl_failure("boom") is False


class TestExtractCrawlError:
    def test_prefers_error_key(self):
        assert (
            crawl_client.extract_crawl_error(
                {"success": False, "error": "boom"}
            )
            == "boom"
        )

    def test_falls_back_to_detail(self):
        assert crawl_client.extract_crawl_error({"detail": "nope"}) == "nope"

    def test_falls_back_to_str(self):
        assert crawl_client.extract_crawl_error("boom") == "boom"


class TestExtractMarkdown:
    def test_extracts_markdown_field(self):
        assert (
            crawl_client.extract_markdown(
                {"markdown": "# hi", "success": True}
            )
            == "# hi"
        )


class TestExtractFirstResult:
    def test_returns_first_item(self):
        body = {"results": [{"url": "a"}, {"url": "b"}]}
        assert crawl_client.extract_first_result(body) == {"url": "a"}

    def test_raises_on_empty_results(self):
        with pytest.raises(ValueError):
            crawl_client.extract_first_result({"results": []})

    def test_raises_on_missing_results(self):
        with pytest.raises(ValueError):
            crawl_client.extract_first_result({})


class TestExtractPageMarkdown:
    def test_plain_string_passthrough(self):
        assert crawl_client.extract_page_markdown("# hi") == "# hi"

    def test_prefers_fit_markdown_from_dict(self):
        field = {"fit_markdown": "fit", "raw_markdown": "raw"}
        assert crawl_client.extract_page_markdown(field) == "fit"

    def test_falls_back_to_raw_markdown_when_fit_is_empty(self):
        field = {"fit_markdown": "", "raw_markdown": "raw"}
        assert crawl_client.extract_page_markdown(field) == "raw"

    def test_empty_dict_returns_empty_string(self):
        assert crawl_client.extract_page_markdown({}) == ""

    def test_none_returns_empty_string(self):
        assert crawl_client.extract_page_markdown(None) == ""


class TestExtractPageLinks:
    def test_internal_only_by_default(self):
        result = {
            "links": {
                "internal": [{"href": "https://x.com/a"}],
                "external": [{"href": "https://y.com/b"}],
            }
        }
        assert crawl_client.extract_page_links(result) == ["https://x.com/a"]

    def test_includes_external_when_requested(self):
        result = {
            "links": {
                "internal": [{"href": "https://x.com/a"}],
                "external": [{"href": "https://y.com/b"}],
            }
        }
        assert crawl_client.extract_page_links(
            result, include_external=True
        ) == [
            "https://x.com/a",
            "https://y.com/b",
        ]

    def test_missing_links_field_returns_empty_list(self):
        assert crawl_client.extract_page_links({}) == []

    def test_skips_entries_without_href(self):
        result = {"links": {"internal": [{"text": "no href"}], "external": []}}
        assert crawl_client.extract_page_links(result) == []


class TestDecodeScreenshot:
    def test_decodes_base64(self):
        import base64

        encoded = base64.b64encode(b"\x89PNG").decode()
        assert (
            crawl_client.decode_screenshot({"screenshot": encoded})
            == b"\x89PNG"
        )


class TestBuildArtifactFilename:
    def test_builds_slug_with_id_and_suffix(self):
        name = crawl_client.build_artifact_filename(
            "https://example.com/a/b?x=1", "fixed-id", ".md"
        )
        assert name == "example-com-a-b-fixed-id.md"

    def test_deterministic_for_same_inputs(self):
        first = crawl_client.build_artifact_filename(
            "https://x.com/p", "id1", ".png"
        )
        second = crawl_client.build_artifact_filename(
            "https://x.com/p", "id1", ".png"
        )
        assert first == second

    def test_falls_back_to_page_for_bare_domain(self):
        name = crawl_client.build_artifact_filename(
            "https://example.com", "id1", ".md"
        )
        assert name == "example-com-id1.md"


# --------------------------------------------------------------------------
# I/O functions: monkeypatch crawl_client.request (or crawl_client.httpx for
# request() itself), so nothing touches the network.
# --------------------------------------------------------------------------


def _fake_request(monkeypatch, return_value=None, error=None):
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
        calls.append(
            {
                "method": method,
                "path": path,
                "base_url": base_url,
                "headers": headers,
                "json_body": json_body,
            }
        )
        if error is not None:
            raise error
        return return_value

    monkeypatch.setattr(crawl_client, "request", fake)
    return calls


class TestPerEndpointFunctions:
    def test_health_uses_get_no_auth(self, monkeypatch):
        calls = _fake_request(monkeypatch, return_value={"status": "ok"})
        result = crawl_client.health("http://base")
        assert result == {"status": "ok"}
        assert calls[0] == {
            "method": "GET",
            "path": "/health",
            "base_url": "http://base",
            "headers": None,
            "json_body": None,
        }

    def test_fetch_markdown_posts_to_md(self, monkeypatch):
        calls = _fake_request(monkeypatch, return_value={"markdown": "# hi"})
        result = crawl_client.fetch_markdown("http://base", {}, "https://x")
        assert result == {"markdown": "# hi"}
        assert calls[0]["method"] == "POST"
        assert calls[0]["path"] == "/md"
        assert calls[0]["json_body"] == {"url": "https://x", "f": "fit"}

    def test_crawl_posts_to_crawl(self, monkeypatch):
        calls = _fake_request(monkeypatch, return_value={"results": []})
        crawl_client.crawl("http://base", {}, ["https://x"])
        assert calls[0]["method"] == "POST"
        assert calls[0]["path"] == "/crawl"
        assert calls[0]["json_body"] == {"urls": ["https://x"]}

    def test_screenshot_posts_to_screenshot(self, monkeypatch):
        calls = _fake_request(monkeypatch, return_value={"screenshot": "abc"})
        crawl_client.screenshot("http://base", {}, "https://x", wait_for=1.0)
        assert calls[0]["method"] == "POST"
        assert calls[0]["path"] == "/screenshot"
        assert calls[0]["json_body"] == {
            "url": "https://x",
            "screenshot_wait_for": 1.0,
        }


class TestRequestErrorHandling:
    def test_raises_crawl4aierror_on_error_response(self, monkeypatch):
        class _FakeResponse:
            status_code = 502
            is_error = True
            url = "http://base/md"

            def json(self):
                return {"detail": "bad gateway"}

        monkeypatch.setattr(
            crawl_client.httpx, "request", lambda *a, **k: _FakeResponse()
        )
        with pytest.raises(crawl_client.Crawl4AIError) as exc_info:
            crawl_client.request("POST", "/md", base_url="http://base")
        assert exc_info.value.status_code == 502
        assert exc_info.value.body == {"detail": "bad gateway"}

    def test_returns_body_on_success(self, monkeypatch):
        class _FakeResponse:
            status_code = 200
            is_error = False
            url = "http://base/health"

            def json(self):
                return {"status": "ok"}

        monkeypatch.setattr(
            crawl_client.httpx, "request", lambda *a, **k: _FakeResponse()
        )
        result = crawl_client.request("GET", "/health", base_url="http://base")
        assert result == {"status": "ok"}


class TestSaveToCache:
    def test_save_bytes_to_cache(self, tmp_path):
        path = crawl_client.save_bytes_to_cache(
            str(tmp_path), "a.png", b"\x89PNG"
        )
        assert Path(path).read_bytes() == b"\x89PNG"

    def test_save_text_to_cache(self, tmp_path):
        path = crawl_client.save_text_to_cache(str(tmp_path), "a.md", "# hi")
        assert Path(path).read_text() == "# hi"

    def test_save_creates_parent_dirs(self, tmp_path):
        nested = str(tmp_path / "nested" / "dir")
        path = crawl_client.save_bytes_to_cache(nested, "a.txt", b"x")
        assert Path(path).exists()
