# `agent`: client for an agent-service API

`gencli agent` talks to an [agent-service](https://github.com/gpadpoll/agent-service)
deployment: LangGraph agents behind a LiteLLM gateway (so any LLM can serve
them), using gencli's own `kb` and `crawl` clients as their tools.

This notebook documents the pure functions in `gencli.agent_client`. The
I/O functions (`list_agents`, `invoke`, `stream_events`, `get_thread`) are
thin wrappers around them.

Configure with:

```bash
gencli config set agent_base_url https://agent-api-<project-number>.us-central1.run.app
gencli config set agent_api_key <key>
```


```python
%load_ext autoreload
%autoreload 2
```

## Auth header: `build_agent_headers`

The agent API accepts an `X-API-Key` header. No key gives no header, for a local
server running with auth disabled.


```python
from gencli.agent_client import build_agent_headers

build_agent_headers("my-key"), build_agent_headers(None)
```




    ({'X-API-Key': 'my-key'}, {})



## Request body: `build_invoke_payload`

The same body works for `/invoke` (run to completion) and `/stream` (SSE).
`thread_id` continues a conversation. `model` picks a gateway alias
(`default`, `smart`, `cheap`, `gemini`, `local`).


```python
from gencli.agent_client import build_invoke_payload

build_invoke_payload("What do we know about alice?", thread_id="t1", model="cheap")
```




    {'input': 'What do we know about alice?', 'thread_id': 't1', 'model': 'cheap'}



Equivalent CLI command:

```bash
gencli agent invoke general "What do we know about alice?" --thread t1 --model cheap
```

Empty input is rejected before any network call:


```python
try:
    build_invoke_payload("   ")
except ValueError as exc:
    print("ValueError:", exc)
```

    ValueError: input text must not be empty


## Streaming: `parse_sse_lines` and `stream_text`

`/stream` sends Server-Sent Events: `start`, `token` (reply text as the
model writes it), `step` (each graph node's output, including tool calls)
and `end`. `parse_sse_lines` turns raw lines into event dicts, and
`stream_text` joins the tokens back into the reply.


```python
from gencli.agent_client import parse_sse_lines, stream_text

lines = [
    "event: start", 'data: {"agent": "general", "thread_id": "t1"}', "",
    "event: token", 'data: {"text": "Alice wears "}', "",
    "event: token", 'data: {"text": "size 8."}', "",
    "event: end", 'data: {"thread_id": "t1"}', "",
]
events = list(parse_sse_lines(lines))
events
```




    [{'event': 'start', 'data': {'agent': 'general', 'thread_id': 't1'}},
     {'event': 'token', 'data': {'text': 'Alice wears '}},
     {'event': 'token', 'data': {'text': 'size 8.'}},
     {'event': 'end', 'data': {'thread_id': 't1'}}]




```python
stream_text(events)
```




    'Alice wears size 8.'



Equivalent CLI commands:

```bash
gencli agent stream general "What do we know about alice?"            # prints tokens
gencli agent stream general "What do we know about alice?" --events   # one JSON event per line
```

## Errors: `extract_error_message`

API errors (unknown agent, bad key) exit with code 1 and print this message
as JSON on stderr.


```python
from gencli.agent_client import extract_error_message

extract_error_message({"detail": "unknown agent 'nope'"})
```




    "unknown agent 'nope'"
