"""
CLI-wiring tests for `gencli kb`.

These monkeypatch `gencli.kb_client.request` — the single chokepoint every
kb_client I/O function funnels through — so no real network call is ever
made. They check argument parsing, exit codes, and basic output shape;
the underlying logic (payload building, value coercion, error-shape
handling) is already covered directly in tests/test_kb_client.py.
"""

import json
import os
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from gencli import kb_client
from gencli.main import app

runner = CliRunner()


def _scripted_request(monkeypatch, responses):
    """
    Patch gencli.kb_client.request with a fake that looks up its return
    value (or raises, if the value is an Exception) from `responses`,
    keyed by (method, path). Returns the list of captured calls.
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
        key = (method, path)
        if key not in responses:
            raise AssertionError(f"unexpected request: {key}")
        result = responses[key]
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(kb_client, "request", fake)
    return calls


@pytest.fixture
def kb_config_path(tmp_path):
    """An isolated GENCLI_CONFIG_PATH per test (no auth, no fixed base_url)."""
    config_path = tmp_path / "config.json"
    with patch.dict(os.environ, {"GENCLI_CONFIG_PATH": str(config_path)}):
        yield config_path


def test_kb_help(kb_config_path):
    result = runner.invoke(app, ["kb", "--help"])
    assert result.exit_code == 0
    assert "Knowledge Base" in result.output


class TestMeCommand:
    def test_me_prints_json(self, kb_config_path, monkeypatch):
        _scripted_request(
            monkeypatch,
            {("GET", "/api/me"): {"email": "dev@localhost", "kind": "dev"}},
        )
        result = runner.invoke(app, ["kb", "me"])
        assert result.exit_code == 0
        assert json.loads(result.output) == {
            "email": "dev@localhost",
            "kind": "dev",
        }


class TestEvidenceCommands:
    def test_create(self, kb_config_path, monkeypatch):
        _scripted_request(
            monkeypatch,
            {("POST", "/api/evidence"): {"id": "e1", "url": "https://x"}},
        )
        result = runner.invoke(
            app, ["kb", "evidence", "create", "https://x", "gs://y"]
        )
        assert result.exit_code == 0
        assert json.loads(result.output)["id"] == "e1"

    def test_list_default_is_json(self, kb_config_path, monkeypatch):
        _scripted_request(
            monkeypatch,
            {("GET", "/api/evidence"): [{"id": "e1", "url": "https://x"}]},
        )
        result = runner.invoke(app, ["kb", "evidence", "list"])
        assert result.exit_code == 0
        assert json.loads(result.output) == [{"id": "e1", "url": "https://x"}]

    def test_list_table_flag_renders_table(self, kb_config_path, monkeypatch):
        _scripted_request(
            monkeypatch,
            {("GET", "/api/evidence"): [{"id": "e1", "url": "https://x"}]},
        )
        result = runner.invoke(app, ["kb", "evidence", "list", "--table"])
        assert result.exit_code == 0
        assert "e1" in result.output
        assert "https://x" in result.output
        with pytest.raises(json.JSONDecodeError):
            json.loads(result.output)

    def test_search(self, kb_config_path, monkeypatch):
        _scripted_request(
            monkeypatch,
            {
                ("GET", "/api/evidence/search"): [
                    {
                        "evidence": {"id": "e1", "url": "https://x"},
                        "score": 0.9,
                    }
                ]
            },
        )
        result = runner.invoke(app, ["kb", "evidence", "search", "x"])
        assert result.exit_code == 0
        assert json.loads(result.output)[0]["evidence"]["id"] == "e1"

    def test_get_not_found_exits_1(self, kb_config_path, monkeypatch):
        _scripted_request(
            monkeypatch,
            {
                ("GET", "/api/evidence/missing"): kb_client.KBAPIError(
                    404, {"detail": "evidence not found"}, "http://x"
                )
            },
        )
        result = runner.invoke(app, ["kb", "evidence", "get", "missing"])
        assert result.exit_code == 1
        payload = json.loads(result.output)
        assert payload == {"error": "evidence not found", "status_code": 404}

    def test_retract(self, kb_config_path, monkeypatch):
        _scripted_request(
            monkeypatch,
            {
                ("POST", "/api/evidence/e1/retract"): {
                    "id": "e1",
                    "retracted_at": "now",
                }
            },
        )
        result = runner.invoke(app, ["kb", "evidence", "retract", "e1"])
        assert result.exit_code == 0
        assert json.loads(result.output)["retracted_at"] == "now"


class TestAttributesCommands:
    def test_create(self, kb_config_path, monkeypatch):
        _scripted_request(
            monkeypatch,
            {
                ("POST", "/api/attributes"): {
                    "attribute": "age",
                    "value_type": "number",
                }
            },
        )
        result = runner.invoke(
            app,
            ["kb", "attributes", "create", "age", "--value-type", "number"],
        )
        assert result.exit_code == 0
        assert json.loads(result.output)["attribute"] == "age"

    def test_create_conflict_exits_1(self, kb_config_path, monkeypatch):
        _scripted_request(
            monkeypatch,
            {
                ("POST", "/api/attributes"): kb_client.KBAPIError(
                    409,
                    {"detail": 'attribute "age" already exists'},
                    "http://x",
                )
            },
        )
        result = runner.invoke(
            app,
            ["kb", "attributes", "create", "age", "--value-type", "number"],
        )
        assert result.exit_code == 1
        assert json.loads(result.output)["status_code"] == 409

    def test_list(self, kb_config_path, monkeypatch):
        _scripted_request(
            monkeypatch, {("GET", "/api/attributes"): [{"attribute": "age"}]}
        )
        result = runner.invoke(app, ["kb", "attributes", "list"])
        assert result.exit_code == 0
        assert json.loads(result.output) == [{"attribute": "age"}]

    def test_get(self, kb_config_path, monkeypatch):
        _scripted_request(
            monkeypatch, {("GET", "/api/attributes/age"): {"attribute": "age"}}
        )
        result = runner.invoke(app, ["kb", "attributes", "get", "age"])
        assert result.exit_code == 0
        assert json.loads(result.output)["attribute"] == "age"

    def test_retract(self, kb_config_path, monkeypatch):
        _scripted_request(
            monkeypatch,
            {("POST", "/api/attributes/age/retract"): {"attribute": "age"}},
        )
        result = runner.invoke(app, ["kb", "attributes", "retract", "age"])
        assert result.exit_code == 0


class TestFactsCommands:
    def test_ingest_with_existing_evidence_id_makes_one_call(
        self, kb_config_path, monkeypatch
    ):
        calls = _scripted_request(
            monkeypatch,
            {
                ("POST", "/api/facts"): {
                    "fact": {"id": 1},
                    "validation_tags": [],
                }
            },
        )
        result = runner.invoke(
            app,
            [
                "kb",
                "facts",
                "ingest",
                "alice",
                "age",
                "--value-type",
                "number",
                "--value",
                "30",
                "--evidence-id",
                "ev1",
            ],
        )
        assert result.exit_code == 0, result.output
        assert len(calls) == 1
        assert calls[0]["json_body"] == {
            "entity": "alice",
            "attribute": "age",
            "value_type": "number",
            "value": 30,
            "evidence_id": "ev1",
        }

    def test_ingest_with_inline_evidence_makes_two_calls_in_order(
        self, kb_config_path, monkeypatch
    ):
        calls = _scripted_request(
            monkeypatch,
            {
                ("POST", "/api/evidence"): {"id": "new-id"},
                ("POST", "/api/facts"): {
                    "fact": {"id": 1},
                    "validation_tags": [],
                },
            },
        )
        result = runner.invoke(
            app,
            [
                "kb",
                "facts",
                "ingest",
                "bob",
                "age",
                "--value-type",
                "number",
                "--value",
                "10",
                "--evidence-url",
                "https://x",
                "--evidence-artifact-path",
                "gs://y",
            ],
        )
        assert result.exit_code == 0, result.output
        assert len(calls) == 2
        assert calls[0]["path"] == "/api/evidence"
        assert calls[1]["path"] == "/api/facts"
        assert calls[1]["json_body"]["evidence_id"] == "new-id"

    def test_ingest_validation_failure_exits_2_and_prints_tags(
        self, kb_config_path, monkeypatch
    ):
        _scripted_request(
            monkeypatch,
            {
                ("POST", "/api/facts"): kb_client.KBAPIError(
                    422,
                    {
                        "detail": {
                            "validation_tags": [
                                {
                                    "rule_name": "attribute_known",
                                    "severity": "blocking",
                                    "passed": False,
                                    "reason": "unknown attribute",
                                }
                            ]
                        }
                    },
                    "http://x",
                )
            },
        )
        result = runner.invoke(
            app,
            [
                "kb",
                "facts",
                "ingest",
                "alice",
                "not-a-real-attribute",
                "--value-type",
                "string",
                "--value",
                "x",
                "--evidence-id",
                "ev1",
            ],
        )
        assert result.exit_code == 2
        payload = json.loads(result.output)
        assert payload["error"] == "validation_failed"
        assert payload["validation_tags"][0]["rule_name"] == "attribute_known"

    def test_ingest_rejects_both_evidence_selectors_without_calling_api(
        self, kb_config_path, monkeypatch
    ):
        calls = _scripted_request(monkeypatch, {})
        result = runner.invoke(
            app,
            [
                "kb",
                "facts",
                "ingest",
                "alice",
                "age",
                "--value-type",
                "number",
                "--value",
                "1",
                "--evidence-id",
                "ev1",
                "--evidence-url",
                "https://x",
                "--evidence-artifact-path",
                "gs://y",
            ],
        )
        assert result.exit_code != 0
        assert calls == []

    def test_ingest_rejects_invalid_value_without_calling_facts_api(
        self, kb_config_path, monkeypatch
    ):
        calls = _scripted_request(monkeypatch, {})
        result = runner.invoke(
            app,
            [
                "kb",
                "facts",
                "ingest",
                "alice",
                "age",
                "--value-type",
                "number",
                "--value",
                "not-a-number",
                "--evidence-id",
                "ev1",
            ],
        )
        assert result.exit_code == 1
        assert calls == []

    def test_get(self, kb_config_path, monkeypatch):
        _scripted_request(
            monkeypatch,
            {("GET", "/api/facts/1"): {"fact": {"id": 1}, "tags": []}},
        )
        result = runner.invoke(app, ["kb", "facts", "get", "1"])
        assert result.exit_code == 0
        assert json.loads(result.output)["fact"]["id"] == 1

    def test_retract(self, kb_config_path, monkeypatch):
        _scripted_request(
            monkeypatch, {("POST", "/api/facts/1/retract"): {"id": 1}}
        )
        result = runner.invoke(app, ["kb", "facts", "retract", "1"])
        assert result.exit_code == 0

    def test_list(self, kb_config_path, monkeypatch):
        _scripted_request(monkeypatch, {("GET", "/api/facts"): [{"id": 1}]})
        result = runner.invoke(app, ["kb", "facts", "list"])
        assert result.exit_code == 0
        assert json.loads(result.output) == [{"id": 1}]

    def test_search(self, kb_config_path, monkeypatch):
        _scripted_request(
            monkeypatch,
            {
                ("GET", "/api/facts/search"): [
                    {"fact": {"id": 1, "attribute": "title"}, "score": 0.8}
                ]
            },
        )
        result = runner.invoke(
            app, ["kb", "facts", "search", "Manager", "--attribute", "title"]
        )
        assert result.exit_code == 0
        assert json.loads(result.output)[0]["fact"]["id"] == 1

    def test_for_entity(self, kb_config_path, monkeypatch):
        _scripted_request(
            monkeypatch,
            {("GET", "/api/facts/alice"): [{"id": 1, "entity": "alice"}]},
        )
        result = runner.invoke(app, ["kb", "facts", "for-entity", "alice"])
        assert result.exit_code == 0
        assert json.loads(result.output) == [{"id": 1, "entity": "alice"}]


class TestEntitiesCommands:
    def test_list(self, kb_config_path, monkeypatch):
        _scripted_request(
            monkeypatch, {("GET", "/api/entities"): ["alice", "bob"]}
        )
        result = runner.invoke(app, ["kb", "entities", "list"])
        assert result.exit_code == 0
        assert json.loads(result.output) == ["alice", "bob"]

    def test_list_table(self, kb_config_path, monkeypatch):
        _scripted_request(
            monkeypatch, {("GET", "/api/entities"): ["alice", "bob"]}
        )
        result = runner.invoke(app, ["kb", "entities", "list", "--table"])
        assert result.exit_code == 0
        assert "alice" in result.output
        assert "bob" in result.output

    def test_search(self, kb_config_path, monkeypatch):
        _scripted_request(
            monkeypatch,
            {
                ("GET", "/api/entities/search"): [
                    {"entity": "alice", "score": 0.95}
                ]
            },
        )
        result = runner.invoke(app, ["kb", "entities", "search", "ali"])
        assert result.exit_code == 0
        assert json.loads(result.output) == [
            {"entity": "alice", "score": 0.95}
        ]


class TestAuthModes:
    def test_none_mode_sends_no_authorization_header(
        self, kb_config_path, monkeypatch
    ):
        calls = _scripted_request(
            monkeypatch, {("GET", "/api/me"): {"email": "dev"}}
        )
        result = runner.invoke(app, ["kb", "me"])
        assert result.exit_code == 0
        assert calls[0]["headers"] == {}

    def test_token_mode_sends_configured_bearer_token(
        self, kb_config_path, monkeypatch
    ):
        kb_config_path.write_text(
            json.dumps({"kb_auth_mode": "token", "kb_token": "raw-token"})
        )
        calls = _scripted_request(
            monkeypatch, {("GET", "/api/me"): {"email": "dev"}}
        )
        result = runner.invoke(app, ["kb", "me"])
        assert result.exit_code == 0
        assert calls[0]["headers"] == {"Authorization": "Bearer raw-token"}

    def test_service_account_mode_mints_token_and_sends_header(
        self, kb_config_path, monkeypatch
    ):
        kb_config_path.write_text(
            json.dumps(
                {
                    "kb_auth_mode": "service_account",
                    "kb_service_account_file": "/tmp/sa.json",
                    "kb_base_url": "https://api.example.com",
                }
            )
        )
        monkeypatch.setattr(
            kb_client,
            "mint_service_account_id_token",
            lambda service_account_file, audience: "minted-token",
        )
        calls = _scripted_request(
            monkeypatch, {("GET", "/api/me"): {"email": "svc"}}
        )
        result = runner.invoke(app, ["kb", "me"])
        assert result.exit_code == 0
        assert calls[0]["headers"] == {"Authorization": "Bearer minted-token"}

    def test_service_account_mode_missing_file_exits_3(
        self, kb_config_path, monkeypatch
    ):
        kb_config_path.write_text(
            json.dumps({"kb_auth_mode": "service_account"})
        )
        result = runner.invoke(app, ["kb", "me"])
        assert result.exit_code == 3
