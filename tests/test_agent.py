"""
CLI-wiring tests for `gencli agent`. They monkeypatch
`gencli.agent_client.request` / `stream_events`, so no network is used;
logic is covered in tests/test_agent_client.py.
"""

import json

from typer.testing import CliRunner

from gencli import agent_client
from gencli.main import app

runner = CliRunner()


def _config(tmp_path, monkeypatch, **extra):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"agent_base_url": "http://agent.test", **extra})
    )
    monkeypatch.setenv("GENCLI_CONFIG_PATH", str(path))


def test_invoke_sends_payload_and_key(tmp_path, monkeypatch):
    key = "secret"  # pragma: allowlist secret
    _config(tmp_path, monkeypatch, agent_api_key=key)
    calls = []

    def fake(method, path, *, base_url, headers=None, json_body=None, **kw):
        calls.append((method, path, base_url, headers, json_body))
        return {"output": "pong", "thread_id": "t1"}

    monkeypatch.setattr(agent_client, "request", fake)
    result = runner.invoke(
        app, ["agent", "invoke", "general", "ping", "-t", "t1", "-m", "cheap"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["output"] == "pong"
    assert calls == [
        (
            "POST",
            "/v1/agents/general/invoke",
            "http://agent.test",
            {"X-API-Key": "secret"},
            {"input": "ping", "thread_id": "t1", "model": "cheap"},
        )
    ]


def test_invoke_output_only(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch)
    monkeypatch.setattr(
        agent_client, "request", lambda *a, **k: {"output": "just text"}
    )
    result = runner.invoke(app, ["agent", "invoke", "general", "hi", "-o"])
    assert result.stdout == "just text\n"


def test_api_error_exit_code(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch)

    def fail(*a, **k):
        raise agent_client.AgentAPIError(404, {"detail": "unknown agent"}, "u")

    monkeypatch.setattr(agent_client, "request", fail)
    result = runner.invoke(app, ["agent", "invoke", "nope", "hi"])
    assert result.exit_code == 1
    assert "unknown agent" in result.stderr


def test_stream_prints_tokens(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch)
    events = [
        {"event": "start", "data": {}},
        {"event": "token", "data": {"text": "Hel"}},
        {"event": "token", "data": {"text": "lo"}},
        {"event": "end", "data": {}},
    ]
    monkeypatch.setattr(
        agent_client, "stream_events", lambda *a, **k: iter(events)
    )
    result = runner.invoke(app, ["agent", "stream", "general", "hi"])
    assert result.exit_code == 0
    assert result.stdout == "Hello\n"


def test_list(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch)
    monkeypatch.setattr(
        agent_client, "request", lambda *a, **k: [{"name": "general"}]
    )
    result = runner.invoke(app, ["agent", "list"])
    assert json.loads(result.stdout) == [{"name": "general"}]
