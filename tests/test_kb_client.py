"""
Test suite for gencli.kb_client.

Pure functions are tested directly with plain asserts. I/O functions are
tested by monkeypatching `kb_client.request` — the single chokepoint every
other I/O function in the module funnels through — so no real network call
is ever made.
"""

import json

import pytest

from gencli import kb_client

# --------------------------------------------------------------------------
# Pure functions
# --------------------------------------------------------------------------


class TestBuildAuthHeader:
    def test_with_token(self):
        assert kb_client.build_auth_header("abc") == {
            "Authorization": "Bearer abc"
        }

    def test_without_token(self):
        assert kb_client.build_auth_header(None) == {}
        assert kb_client.build_auth_header("") == {}


class TestBuildEvidenceCreatePayload:
    def test_with_media_type(self):
        assert kb_client.build_evidence_create_payload(
            "https://example.com", "gs://bucket/a.png", "image/png"
        ) == {
            "url": "https://example.com",
            "artifact_path": "gs://bucket/a.png",
            "media_type": "image/png",
        }

    def test_without_media_type(self):
        payload = kb_client.build_evidence_create_payload(
            "https://example.com", "gs://bucket/a.png"
        )
        assert payload["media_type"] is None


class TestBuildAttributeCreatePayload:
    def test_defaults(self):
        payload = kb_client.build_attribute_create_payload("age", "number")
        assert payload == {
            "attribute": "age",
            "value_type": "number",
            "cardinality": "many",
            "description": None,
        }

    def test_explicit_values(self):
        payload = kb_client.build_attribute_create_payload(
            "title", "string", cardinality="one", description="job title"
        )
        assert payload["cardinality"] == "one"
        assert payload["description"] == "job title"


class TestCoerceFactValue:
    def test_string_and_entity_and_datetime_passthrough(self):
        assert kb_client.coerce_fact_value("alice", "entity") == "alice"
        assert kb_client.coerce_fact_value("hello", "string") == "hello"
        assert (
            kb_client.coerce_fact_value("2024-01-01T00:00:00Z", "datetime")
            == "2024-01-01T00:00:00Z"
        )

    def test_number_int_and_float(self):
        assert kb_client.coerce_fact_value("42", "number") == 42
        assert isinstance(kb_client.coerce_fact_value("42", "number"), int)
        assert kb_client.coerce_fact_value("3.14", "number") == 3.14
        assert kb_client.coerce_fact_value("1e3", "number") == 1000.0

    def test_number_invalid(self):
        with pytest.raises(ValueError):
            kb_client.coerce_fact_value("not-a-number", "number")

    def test_boolean_variants(self):
        for truthy in ("true", "1", "yes", "TRUE", "Yes"):
            assert kb_client.coerce_fact_value(truthy, "boolean") is True
        for falsy in ("false", "0", "no", "FALSE"):
            assert kb_client.coerce_fact_value(falsy, "boolean") is False

    def test_boolean_invalid(self):
        with pytest.raises(ValueError):
            kb_client.coerce_fact_value("maybe", "boolean")

    def test_json_valid(self):
        assert kb_client.coerce_fact_value('{"a": 1}', "json") == {"a": 1}
        assert kb_client.coerce_fact_value("[1, 2]", "json") == [1, 2]

    def test_json_invalid(self):
        with pytest.raises(json.JSONDecodeError):
            kb_client.coerce_fact_value("{not json", "json")

    def test_unknown_value_type(self):
        with pytest.raises(ValueError):
            kb_client.coerce_fact_value("x", "not-a-real-type")


class TestBuildFactCreatePayload:
    def test_builds_expected_shape(self):
        payload = kb_client.build_fact_create_payload(
            "alice", "age", "number", 30, "evidence-uuid"
        )
        assert payload == {
            "entity": "alice",
            "attribute": "age",
            "value_type": "number",
            "value": 30,
            "evidence_id": "evidence-uuid",
        }


class TestBuildRetractFactPayload:
    def test_with_and_without_evidence(self):
        assert kb_client.build_retract_fact_payload() == {
            "retraction_evidence_id": None
        }
        assert kb_client.build_retract_fact_payload("uuid") == {
            "retraction_evidence_id": "uuid"
        }


class TestValidateEvidenceSelector:
    def test_evidence_id_only_is_valid(self):
        kb_client.validate_evidence_selector("id", None, None)  # no raise

    def test_url_and_path_is_valid(self):
        kb_client.validate_evidence_selector(None, "url", "path")  # no raise

    def test_both_given_is_invalid(self):
        with pytest.raises(ValueError):
            kb_client.validate_evidence_selector("id", "url", "path")

    def test_neither_given_is_invalid(self):
        with pytest.raises(ValueError):
            kb_client.validate_evidence_selector(None, None, None)

    def test_only_url_is_invalid(self):
        with pytest.raises(ValueError):
            kb_client.validate_evidence_selector(None, "url", None)

    def test_only_path_is_invalid(self):
        with pytest.raises(ValueError):
            kb_client.validate_evidence_selector(None, None, "path")


class TestResolveApiAudience:
    def test_explicit_override(self):
        assert (
            kb_client.resolve_api_audience("http://base", "http://override")
            == "http://override"
        )

    def test_defaults_to_base_url(self):
        assert (
            kb_client.resolve_api_audience("http://base", None)
            == "http://base"
        )


class TestIsValidationFailure:
    def test_matches_exact_422_shape(self):
        body = {"detail": {"validation_tags": [{"rule_name": "x"}]}}
        assert kb_client.is_validation_failure(422, body) is True

    def test_normal_404_shape_is_not_a_validation_failure(self):
        assert (
            kb_client.is_validation_failure(404, {"detail": "not found"})
            is False
        )

    def test_non_dict_body_is_not_a_validation_failure(self):
        assert kb_client.is_validation_failure(422, "some text") is False

    def test_wrong_status_code_is_not_a_validation_failure(self):
        body = {"detail": {"validation_tags": []}}
        assert kb_client.is_validation_failure(400, body) is False


class TestExtractValidationTags:
    def test_extracts_the_list(self):
        tags = [{"rule_name": "attribute_known", "passed": False}]
        body = {"detail": {"validation_tags": tags}}
        assert kb_client.extract_validation_tags(body) == tags


class TestExtractErrorMessage:
    def test_plain_string_detail(self):
        assert (
            kb_client.extract_error_message(404, {"detail": "not found"})
            == "not found"
        )

    def test_falls_back_for_object_detail(self):
        body = {"detail": {"validation_tags": []}}
        assert kb_client.extract_error_message(422, body) == str(body)

    def test_falls_back_for_non_dict_body(self):
        assert kb_client.extract_error_message(500, "boom") == "boom"


class TestFlattenSearchResult:
    def test_flattens_nested_object(self):
        item = {"evidence": {"id": "e1", "url": "http://x"}, "score": 0.9}
        assert kb_client.flatten_search_result(item, "evidence") == {
            "id": "e1",
            "url": "http://x",
            "score": 0.9,
        }


# --------------------------------------------------------------------------
# I/O functions: monkeypatch kb_client.request (or, for token minting,
# kb_client.service_account) so nothing touches the network.
# --------------------------------------------------------------------------


def _fake_request(monkeypatch, return_value=None, error=None):
    """Patch kb_client.request; returns the list of captured call kwargs."""
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
                "params": params,
                "json_body": json_body,
            }
        )
        if error is not None:
            raise error
        return return_value

    monkeypatch.setattr(kb_client, "request", fake)
    return calls


class TestPerEndpointFunctions:
    def test_me(self, monkeypatch):
        calls = _fake_request(monkeypatch, return_value={"email": "a@b.com"})
        result = kb_client.me("http://base", {"Authorization": "Bearer t"})
        assert result == {"email": "a@b.com"}
        assert calls == [
            {
                "method": "GET",
                "path": "/api/me",
                "base_url": "http://base",
                "headers": {"Authorization": "Bearer t"},
                "params": None,
                "json_body": None,
            }
        ]

    def test_create_evidence_posts_expected_payload(self, monkeypatch):
        calls = _fake_request(monkeypatch, return_value={"id": "e1"})
        result = kb_client.create_evidence(
            "http://base", {}, "https://x", "gs://y", "text/html"
        )
        assert result == {"id": "e1"}
        assert calls[0]["method"] == "POST"
        assert calls[0]["path"] == "/api/evidence"
        assert calls[0]["json_body"] == {
            "url": "https://x",
            "artifact_path": "gs://y",
            "media_type": "text/html",
        }

    def test_list_evidence_uses_get_with_params(self, monkeypatch):
        calls = _fake_request(monkeypatch, return_value=[])
        kb_client.list_evidence(
            "http://base", {}, include_retracted=True, limit=5
        )
        assert calls[0]["method"] == "GET"
        assert calls[0]["path"] == "/api/evidence"
        assert calls[0]["params"] == {"include_retracted": True, "limit": 5}

    def test_search_facts_omits_attribute_param_when_none(self, monkeypatch):
        calls = _fake_request(monkeypatch, return_value=[])
        kb_client.search_facts("http://base", {}, "Manager")
        assert calls[0]["params"] == {"q": "Manager", "limit": 20}

    def test_search_facts_includes_attribute_param_when_given(
        self, monkeypatch
    ):
        calls = _fake_request(monkeypatch, return_value=[])
        kb_client.search_facts("http://base", {}, "Manager", attribute="title")
        assert calls[0]["params"] == {
            "q": "Manager",
            "limit": 20,
            "attribute": "title",
        }

    def test_create_fact_posts_expected_payload(self, monkeypatch):
        calls = _fake_request(monkeypatch, return_value={"fact": {"id": 1}})
        kb_client.create_fact(
            "http://base", {}, "alice", "age", "number", 30, "ev1"
        )
        assert calls[0]["method"] == "POST"
        assert calls[0]["path"] == "/api/facts"
        assert calls[0]["json_body"] == {
            "entity": "alice",
            "attribute": "age",
            "value_type": "number",
            "value": 30,
            "evidence_id": "ev1",
        }

    def test_retract_fact_posts_retraction_payload(self, monkeypatch):
        calls = _fake_request(monkeypatch, return_value={"id": 1})
        kb_client.retract_fact(
            "http://base", {}, 1, retraction_evidence_id="ev2"
        )
        assert calls[0]["path"] == "/api/facts/1/retract"
        assert calls[0]["json_body"] == {"retraction_evidence_id": "ev2"}

    def test_get_facts_for_entity_uses_entity_path(self, monkeypatch):
        calls = _fake_request(monkeypatch, return_value=[])
        kb_client.get_facts_for_entity("http://base", {}, "alice")
        assert calls[0]["path"] == "/api/facts/alice"

    def test_list_entities_uses_limit_param(self, monkeypatch):
        calls = _fake_request(monkeypatch, return_value=[])
        kb_client.list_entities("http://base", {}, limit=7)
        assert calls[0]["path"] == "/api/entities"
        assert calls[0]["params"] == {"limit": 7}

    def test_retract_attribute_posts_to_retract_path(self, monkeypatch):
        calls = _fake_request(monkeypatch, return_value={"attribute": "age"})
        kb_client.retract_attribute("http://base", {}, "age")
        assert calls[0]["method"] == "POST"
        assert calls[0]["path"] == "/api/attributes/age/retract"


class TestResolveEvidenceId:
    def test_reuses_existing_id_without_network_call(self, monkeypatch):
        def fail_if_called(*args, **kwargs):
            raise AssertionError("request() should not be called")

        monkeypatch.setattr(kb_client, "request", fail_if_called)
        result = kb_client.resolve_evidence_id(
            "http://base", {}, evidence_id="existing-id"
        )
        assert result == "existing-id"

    def test_creates_evidence_inline_and_returns_new_id(self, monkeypatch):
        calls = _fake_request(monkeypatch, return_value={"id": "new-uuid"})
        result = kb_client.resolve_evidence_id(
            "http://base",
            {},
            evidence_url="https://x",
            artifact_path="gs://y",
        )
        assert result == "new-uuid"
        assert len(calls) == 1
        assert calls[0]["method"] == "POST"
        assert calls[0]["path"] == "/api/evidence"

    def test_invalid_selector_raises_before_any_network_call(
        self, monkeypatch
    ):
        def fail_if_called(*args, **kwargs):
            raise AssertionError("request() should not be called")

        monkeypatch.setattr(kb_client, "request", fail_if_called)
        with pytest.raises(ValueError):
            kb_client.resolve_evidence_id("http://base", {})


class _FakeCredentials:
    def __init__(self, token):
        self._token_to_set = token
        self.token = None

    def refresh(self, request):
        self.token = self._token_to_set


class _FakeIDTokenCredentials:
    last_kwargs = None

    @classmethod
    def from_service_account_file(cls, service_account_file, target_audience):
        cls.last_kwargs = {
            "service_account_file": service_account_file,
            "target_audience": target_audience,
        }
        return _FakeCredentials("fake-token")


class TestMintServiceAccountIdToken:
    def test_mints_and_returns_token(self, monkeypatch):
        monkeypatch.setattr(
            kb_client.service_account,
            "IDTokenCredentials",
            _FakeIDTokenCredentials,
        )
        token = kb_client.mint_service_account_id_token(
            "/tmp/sa.json", "https://api.example.com"
        )
        assert token == "fake-token"
        assert _FakeIDTokenCredentials.last_kwargs == {
            "service_account_file": "/tmp/sa.json",
            "target_audience": "https://api.example.com",
        }


class TestBuildHeadersForConfig:
    def test_none_mode_sends_no_header(self):
        assert (
            kb_client.build_headers_for_config({"kb_auth_mode": "none"}) == {}
        )

    def test_missing_mode_defaults_to_none(self):
        assert kb_client.build_headers_for_config({}) == {}

    def test_token_mode_uses_configured_token(self):
        headers = kb_client.build_headers_for_config(
            {"kb_auth_mode": "token", "kb_token": "raw-token"}
        )
        assert headers == {"Authorization": "Bearer raw-token"}

    def test_service_account_mode_mints_and_uses_token(self, monkeypatch):
        captured = {}

        def fake_mint(service_account_file, audience):
            captured["service_account_file"] = service_account_file
            captured["audience"] = audience
            return "minted-token"

        monkeypatch.setattr(
            kb_client, "mint_service_account_id_token", fake_mint
        )
        headers = kb_client.build_headers_for_config(
            {
                "kb_auth_mode": "service_account",
                "kb_service_account_file": "/tmp/sa.json",
                "kb_base_url": "https://api.example.com",
            }
        )
        assert headers == {"Authorization": "Bearer minted-token"}
        assert captured == {
            "service_account_file": "/tmp/sa.json",
            "audience": "https://api.example.com",
        }

    def test_service_account_mode_without_file_raises(self):
        with pytest.raises(ValueError):
            kb_client.build_headers_for_config(
                {"kb_auth_mode": "service_account"}
            )

    def test_unknown_mode_raises(self):
        with pytest.raises(ValueError):
            kb_client.build_headers_for_config(
                {"kb_auth_mode": "carrier-pigeon"}
            )


class TestRequestErrorHandling:
    def test_raises_kbapierror_on_error_response(self, monkeypatch):
        class _FakeResponse:
            status_code = 404
            is_error = True
            url = "http://base/api/facts/999"

            def json(self):
                return {"detail": "fact not found"}

        monkeypatch.setattr(
            kb_client.httpx, "request", lambda *a, **k: _FakeResponse()
        )
        with pytest.raises(kb_client.KBAPIError) as exc_info:
            kb_client.request("GET", "/api/facts/999", base_url="http://base")
        assert exc_info.value.status_code == 404
        assert exc_info.value.body == {"detail": "fact not found"}

    def test_returns_body_on_success(self, monkeypatch):
        class _FakeResponse:
            status_code = 200
            is_error = False
            url = "http://base/api/me"

            def json(self):
                return {"email": "a@b.com"}

        monkeypatch.setattr(
            kb_client.httpx, "request", lambda *a, **k: _FakeResponse()
        )
        result = kb_client.request("GET", "/api/me", base_url="http://base")
        assert result == {"email": "a@b.com"}
