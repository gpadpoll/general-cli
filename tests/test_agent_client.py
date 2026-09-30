"""Direct tests for `gencli.agent_client`'s pure functions."""

import pytest

from gencli import agent_client as ac


class TestBuildAgentHeaders:
    def test_with_key(self):
        assert ac.build_agent_headers("k") == {"X-API-Key": "k"}

    def test_without_key(self):
        assert ac.build_agent_headers(None) == {}
        assert ac.build_agent_headers("") == {}


class TestBuildInvokePayload:
    def test_minimal(self):
        assert ac.build_invoke_payload("hi") == {"input": "hi"}

    def test_with_thread_and_model(self):
        assert ac.build_invoke_payload("hi", "t1", "cheap") == {
            "input": "hi",
            "thread_id": "t1",
            "model": "cheap",
        }

    def test_empty_input_raises(self):
        with pytest.raises(ValueError):
            ac.build_invoke_payload("   ")


class TestParseSseLines:
    def test_events_and_json_data(self):
        lines = [
            "event: start",
            'data: {"thread_id": "t"}',
            "",
            "event: token",
            'data: {"text": "Hel"}',
            "",
            "event: token",
            'data: {"text": "lo"}',
        ]
        events = list(ac.parse_sse_lines(lines))
        assert events[0] == {"event": "start", "data": {"thread_id": "t"}}
        assert ac.stream_text(events) == "Hello"

    def test_multiline_plain_data_and_comments(self):
        lines = [": keep-alive", "data: one", "data: two", ""]
        assert list(ac.parse_sse_lines(lines)) == [
            {"event": "message", "data": "one\ntwo"}
        ]

    def test_empty(self):
        assert list(ac.parse_sse_lines([])) == []


class TestExtractErrorMessage:
    def test_detail(self):
        assert ac.extract_error_message({"detail": "nope"}) == "nope"

    def test_other_shapes(self):
        assert ac.extract_error_message("plain") == "plain"
        assert ac.extract_error_message({"x": 1}) == '{"x": 1}'
