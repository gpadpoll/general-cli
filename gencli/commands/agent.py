"""
Client command group for an agent-service HTTP API.

Thin Typer wrappers only: parse arguments, call one `gencli.agent_client`
function, print the result, and translate errors into exit codes. All real
logic lives in `gencli/agent_client.py` and is unit-tested there.

Exit codes:
    0  success
    1  the agent API returned an error (unknown agent, bad key, ...)
    3  the agent API couldn't be reached, or the input was invalid

Configure with `gencli config set agent_base_url ...` / `agent_api_key`.
"""

import json
import sys
from typing import Any, Callable, Dict, NoReturn, Optional, Tuple, TypeVar

import httpx
import typer

from gencli import agent_client as ac
from gencli.commands.config import DEFAULT_CONFIG, load_config

app = typer.Typer(
    no_args_is_help=True,
    help="Run agents on an agent-service API (LLM-routed agentic workflows).",
)

T = TypeVar("T")

THREAD_OPTION = typer.Option(
    None, "--thread", "-t", help="Thread id: reuse it to continue a chat."
)
MODEL_OPTION = typer.Option(
    None,
    "--model",
    "-m",
    help="Gateway model alias (default, smart, cheap, gemini, local).",
)


def _fail(payload: Dict[str, Any], exit_code: int) -> NoReturn:
    print(json.dumps(payload, default=str), file=sys.stderr)
    raise typer.Exit(exit_code)


def _call(fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    try:
        return fn(*args, **kwargs)
    except ac.AgentAPIError as exc:
        _fail(
            {
                "error": ac.extract_error_message(exc.body),
                "status_code": exc.status_code,
            },
            1,
        )
    except ValueError as exc:
        _fail({"error": str(exc), "status_code": None}, 3)
    except httpx.RequestError as exc:
        _fail({"error": str(exc), "status_code": None}, 3)


def _context() -> Tuple[str, Dict[str, str]]:
    cfg = load_config()
    base_url = str(
        cfg.get("agent_base_url") or DEFAULT_CONFIG["agent_base_url"]
    )
    return base_url, ac.build_agent_headers(cfg.get("agent_api_key"))


def _print_json(data: Any) -> None:
    print(json.dumps(data, default=str, ensure_ascii=False))


@app.command("list")
def list_() -> None:
    """List the agents the service offers."""
    base_url, headers = _context()
    _print_json(_call(ac.list_agents, base_url, headers))


@app.command()
def invoke(
    name: str = typer.Argument(..., help="Agent name, e.g. general."),
    text: str = typer.Argument(..., help="What to ask or do."),
    thread: Optional[str] = THREAD_OPTION,
    model: Optional[str] = MODEL_OPTION,
    output_only: bool = typer.Option(
        False, "--output-only", "-o", help="Print only the reply text."
    ),
) -> None:
    """Run an agent to completion and print its result as JSON."""
    base_url, headers = _context()
    result = _call(ac.invoke, base_url, headers, name, text, thread, model)
    if output_only:
        print(result.get("output", ""))
    else:
        _print_json(result)


@app.command()
def stream(
    name: str = typer.Argument(..., help="Agent name, e.g. general."),
    text: str = typer.Argument(..., help="What to ask or do."),
    thread: Optional[str] = THREAD_OPTION,
    model: Optional[str] = MODEL_OPTION,
    events: bool = typer.Option(
        False, "--events", help="Print every SSE event as a JSON line."
    ),
) -> None:
    """Run an agent, printing its reply as it streams."""
    base_url, headers = _context()

    def run() -> None:
        for event in ac.stream_events(
            base_url, headers, name, text, thread, model
        ):
            if events:
                _print_json(event)
            elif event["event"] == "token":
                print(ac.stream_text([event]), end="", flush=True)
            elif event["event"] == "error":
                _fail({"error": event["data"], "status_code": None}, 1)
        if not events:
            print()

    _call(run)


@app.command()
def thread(
    name: str = typer.Argument(..., help="Agent name."),
    thread_id: str = typer.Argument(..., help="Thread id."),
) -> None:
    """Show a thread's message history."""
    base_url, headers = _context()
    _print_json(_call(ac.get_thread, base_url, headers, name, thread_id))
