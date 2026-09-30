"""
Client library for an agent-service HTTP API (`gpadpoll/agent-service`):
LangGraph agents behind a LiteLLM gateway, served over HTTP.

Two layers, per AGENTS.md's functional-programming rules:

1. Pure functions: payload/header builders, the SSE line parser, and
   response shaping. No I/O, deterministic for a given input.
2. I/O boundary functions: the single low-level HTTP call (`request`) and
   the streaming call (`stream_events`). Per-endpoint functions funnel
   through `request`, so it's the one place CLI-wiring tests monkeypatch.
"""

import json
from typing import Any, Dict, Iterable, Iterator, List, Optional

import httpx


class AgentAPIError(Exception):
    """Raised when the agent API returns a non-2xx response."""

    def __init__(self, status_code: int, body: Any, url: str) -> None:
        self.status_code = status_code
        self.body = body
        self.url = url
        super().__init__(f"{status_code} from {url}: {body}")


# --------------------------------------------------------------------------
# Pure functions (no I/O, no side effects, deterministic output for a given
# input). See AGENTS.md's functional programming rules.
# --------------------------------------------------------------------------


def build_agent_headers(api_key: Optional[str]) -> Dict[str, str]:
    """The ``X-API-Key`` header dict, or ``{}`` when no key is set."""
    return {"X-API-Key": api_key} if api_key else {}


def build_invoke_payload(
    text: str, thread_id: Optional[str] = None, model: Optional[str] = None
) -> Dict[str, Any]:
    """
    Build the body for ``POST /v1/agents/{name}/invoke`` (and ``/stream``).
    ``thread_id`` continues a conversation; ``model`` is a gateway alias
    (``default``, ``smart``, ``cheap``, ``gemini``, ``local``).

    Raises:
        ValueError: if ``text`` is empty or only whitespace.
    """
    if not text.strip():
        raise ValueError("input text must not be empty")
    payload: Dict[str, Any] = {"input": text}
    if thread_id is not None:
        payload["thread_id"] = thread_id
    if model is not None:
        payload["model"] = model
    return payload


def parse_sse_lines(lines: Iterable[str]) -> Iterator[Dict[str, Any]]:
    """
    Parse Server-Sent Events lines into ``{"event": ..., "data": ...}``
    dicts (``data`` JSON-decoded when possible). An event ends at a blank
    line; multiple ``data:`` lines are joined with newlines, per the SSE
    spec. Comment lines (starting with ``:``) are ignored.
    """
    event: Optional[str] = None
    data: List[str] = []
    for raw in list(lines) + [""]:
        line = raw.rstrip("\r\n")
        if not line:
            if event is not None or data:
                joined = "\n".join(data)
                try:
                    decoded: Any = json.loads(joined)
                except ValueError:
                    decoded = joined
                yield {"event": event or "message", "data": decoded}
            event, data = None, []
        elif line.startswith(":"):
            continue
        elif line.startswith("event:"):
            event = line.removeprefix("event:").strip()
        elif line.startswith("data:"):
            data.append(line.removeprefix("data:").lstrip())


def stream_text(events: Iterable[Dict[str, Any]]) -> str:
    """Concatenate the ``token`` events' text: the streamed reply."""
    return "".join(
        e["data"].get("text", "")
        for e in events
        if e["event"] == "token" and isinstance(e["data"], dict)
    )


def extract_error_message(body: Any) -> str:
    """A readable message from an agent API error body."""
    if isinstance(body, dict) and isinstance(body.get("detail"), str):
        return body["detail"]
    return json.dumps(body) if not isinstance(body, str) else body


# --------------------------------------------------------------------------
# I/O boundary
# --------------------------------------------------------------------------


def request(
    method: str,
    path: str,
    *,
    base_url: str,
    headers: Optional[Dict[str, str]] = None,
    json_body: Optional[Dict[str, Any]] = None,
    timeout: float = 300.0,
) -> Any:
    """
    The single low-level HTTP call every non-streaming function funnels
    through. Agent runs can take minutes, hence the long default timeout.

    Raises:
        AgentAPIError: on a non-2xx response.
        httpx.RequestError: on a transport-level failure.
    """
    response = httpx.request(
        method,
        f"{base_url.rstrip('/')}{path}",
        headers=headers,
        json=json_body,
        timeout=timeout,
    )
    try:
        body = response.json()
    except ValueError:
        body = response.text
    if response.is_error:
        raise AgentAPIError(response.status_code, body, str(response.url))
    return body


def list_agents(base_url: str, headers: Dict[str, str]) -> List[Dict]:
    return request("GET", "/v1/agents", base_url=base_url, headers=headers)


def invoke(
    base_url: str,
    headers: Dict[str, str],
    name: str,
    text: str,
    thread_id: Optional[str] = None,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    payload = build_invoke_payload(text, thread_id, model)
    return request(
        "POST",
        f"/v1/agents/{name}/invoke",
        base_url=base_url,
        headers=headers,
        json_body=payload,
    )


def get_thread(
    base_url: str, headers: Dict[str, str], name: str, thread_id: str
) -> Dict[str, Any]:
    return request(
        "GET",
        f"/v1/agents/{name}/threads/{thread_id}",
        base_url=base_url,
        headers=headers,
    )


def stream_events(
    base_url: str,
    headers: Dict[str, str],
    name: str,
    text: str,
    thread_id: Optional[str] = None,
    model: Optional[str] = None,
    timeout: float = 300.0,
) -> Iterator[Dict[str, Any]]:
    """Yield parsed SSE events from ``POST /v1/agents/{name}/stream``."""
    payload = build_invoke_payload(text, thread_id, model)
    url = f"{base_url.rstrip('/')}/v1/agents/{name}/stream"
    with httpx.stream(
        "POST", url, headers=headers, json=payload, timeout=timeout
    ) as response:
        if response.is_error:
            response.read()
            try:
                body: Any = response.json()
            except ValueError:
                body = response.text
            raise AgentAPIError(response.status_code, body, url)
        buffer: List[str] = []
        for line in response.iter_lines():
            buffer.append(line)
            if line == "":
                yield from parse_sse_lines(buffer)
                buffer = []
        if buffer:
            yield from parse_sse_lines(buffer)
