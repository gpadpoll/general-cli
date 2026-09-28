"""
Client command group for the external Knowledge Base ("kb") HTTP API.

This module contains *only* thin Typer command wrappers: parse arguments,
call one `gencli.kb_client` function, render the result, and translate
errors into exit codes. All real logic (payload building, value coercion,
error-shape handling, the HTTP call itself) lives in `gencli/kb_client.py`
and is unit-tested there — see AGENTS.md.

Output is JSON by default on every command (this CLI's primary audience is
agents); list/search commands additionally accept `--table` for a
human-readable Rich table.

Exit codes:
    0  success
    1  a generic API or local-argument error
    2  a fact ingest was blocked by a Knowledge Base validation rule
       (the API's 422-with-validation_tags response) — distinct from 1 so
       a caller can branch on "a rule blocked this" vs. "something else
       broke"
    3  the API couldn't be reached at all, or kb_auth_mode/config itself
       is misconfigured (e.g. a missing service-account file)

Configure with `gencli config set kb_base_url ...` / `kb_auth_mode` /
`kb_token` / `kb_service_account_file` / `kb_api_audience`.
"""

import json
import sys
from enum import Enum
from typing import (
    Any,
    Callable,
    Dict,
    List,
    NoReturn,
    Optional,
    Tuple,
    TypeVar,
)

import google.auth.exceptions
import httpx
import typer
from rich.console import Console
from rich.table import Table

from gencli import kb_client as kc
from gencli.commands.config import DEFAULT_CONFIG, load_config

app = typer.Typer(
    no_args_is_help=True,
    help="Client for the external Knowledge Base HTTP API.",
)
evidence_app = typer.Typer(
    no_args_is_help=True, help="Manage evidence records."
)
attributes_app = typer.Typer(
    no_args_is_help=True, help="Manage attribute definitions."
)
facts_app = typer.Typer(
    no_args_is_help=True, help="Ingest, inspect, and retract facts."
)
entities_app = typer.Typer(
    no_args_is_help=True, help="List and search entities."
)
app.add_typer(evidence_app, name="evidence")
app.add_typer(attributes_app, name="attributes")
app.add_typer(facts_app, name="facts")
app.add_typer(entities_app, name="entities")

console = Console()

T = TypeVar("T")

TABLE_OPTION = typer.Option(
    False, "--table", help="Render as a Rich table instead of JSON."
)


class ValueType(str, Enum):
    entity = "entity"
    string = "string"
    number = "number"
    boolean = "boolean"
    datetime = "datetime"
    json = "json"


class Cardinality(str, Enum):
    one = "one"
    many = "many"


# --------------------------------------------------------------------------
# Shared plumbing: config -> auth headers, error -> exit code, rendering.
# --------------------------------------------------------------------------


def _fail(payload: Dict[str, Any], exit_code: int) -> NoReturn:
    # Plain print, not err_console.print_json: JSON output must always be
    # clean and machine-parseable, never carrying Rich's ANSI styling.
    print(json.dumps(payload, default=str), file=sys.stderr)
    raise typer.Exit(exit_code)


def _fail_value_error(exc: Exception) -> NoReturn:
    _fail({"error": str(exc), "status_code": None}, 1)


def _fail_config_error(exc: Exception) -> NoReturn:
    _fail({"error": str(exc), "status_code": None}, 3)


def _fail_connection(exc: Exception) -> NoReturn:
    _fail({"error": str(exc), "status_code": None}, 3)


def _fail_api(exc: kc.KBAPIError) -> NoReturn:
    if kc.is_validation_failure(exc.status_code, exc.body):
        _fail(
            {
                "error": "validation_failed",
                "status_code": 422,
                "validation_tags": kc.extract_validation_tags(exc.body),
            },
            2,
        )
    _fail(
        {
            "error": kc.extract_error_message(exc.status_code, exc.body),
            "status_code": exc.status_code,
        },
        1,
    )


def _call(fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Call a kb_client I/O function, translating errors to exit codes."""
    try:
        return fn(*args, **kwargs)
    except kc.KBAPIError as exc:
        _fail_api(exc)
    except httpx.RequestError as exc:
        _fail_connection(exc)


def _client_context() -> Tuple[str, Dict[str, str]]:
    """Load config and build (base_url, auth headers), or exit(3) trying."""
    cfg = load_config()
    base_url = str(cfg.get("kb_base_url") or DEFAULT_CONFIG["kb_base_url"])
    try:
        headers = kc.build_headers_for_config(cfg)
    except ValueError as exc:
        _fail_config_error(exc)
    except google.auth.exceptions.GoogleAuthError as exc:
        _fail_config_error(exc)
    return base_url, headers


def _print_json(data: Any) -> None:
    # Plain print, not console.print_json: JSON output must always be
    # clean and machine-parseable, never carrying Rich's ANSI styling.
    print(json.dumps(data, default=str))


def _print_table(rows: List[Dict[str, Any]]) -> None:
    if not rows:
        console.print("[yellow]No results.[/yellow]")
        return
    columns = list(rows[0].keys())
    table = Table()
    for column in columns:
        table.add_column(column)
    for row in rows:
        table.add_row(*[str(row.get(column, "")) for column in columns])
    console.print(table)


def _render_list(
    data: List[Any], *, table: bool, inner_key: Optional[str] = None
) -> None:
    """
    Render a list response: raw JSON by default, or a flattened Rich table
    when `table` is set. `inner_key` names the nested object key for search
    results shaped like {<inner_key>: {...}, "score": ...}.
    """
    if not table:
        _print_json(data)
        return
    if inner_key is not None:
        rows = [kc.flatten_search_result(item, inner_key) for item in data]
    elif data and isinstance(data[0], str):
        rows = [{"value": item} for item in data]
    else:
        rows = data
    _print_table(rows)


# --------------------------------------------------------------------------
# me
# --------------------------------------------------------------------------


@app.command()
def me() -> None:
    """Show the signed-in identity for the configured auth mode."""
    base_url, headers = _client_context()
    data = _call(kc.me, base_url, headers)
    _print_json(data)


# --------------------------------------------------------------------------
# evidence
# --------------------------------------------------------------------------


@evidence_app.command("create")
def create_evidence(
    url: str = typer.Argument(
        ..., help="Source URL this evidence was captured from."
    ),
    artifact_path: str = typer.Argument(
        ...,
        help="Durable snapshot's storage URL (e.g. a screenshot/HTML dump).",
    ),
    media_type: Optional[str] = typer.Option(
        None, help="MIME type of the artifact."
    ),
) -> None:
    """Register evidence."""
    base_url, headers = _client_context()
    data = _call(
        kc.create_evidence, base_url, headers, url, artifact_path, media_type
    )
    _print_json(data)


@evidence_app.command("list")
def list_evidence(
    include_retracted: bool = typer.Option(
        False, help="Include retracted evidence."
    ),
    limit: int = typer.Option(50, help="Maximum rows to return."),
    table: bool = TABLE_OPTION,
) -> None:
    """List evidence, most recent first."""
    base_url, headers = _client_context()
    data = _call(kc.list_evidence, base_url, headers, include_retracted, limit)
    _render_list(data, table=table)


@evidence_app.command("search")
def search_evidence(
    q: str = typer.Argument(
        ..., help="Free-text query, matched against evidence urls."
    ),
    limit: int = typer.Option(20, help="Maximum rows to return."),
    table: bool = TABLE_OPTION,
) -> None:
    """Fuzzy-search evidence by source url."""
    base_url, headers = _client_context()
    data = _call(kc.search_evidence, base_url, headers, q, limit)
    _render_list(data, table=table, inner_key="evidence")


@evidence_app.command("get")
def get_evidence(
    evidence_id: str = typer.Argument(..., help="Evidence id (UUID).")
) -> None:
    """Get a single evidence row by id."""
    base_url, headers = _client_context()
    data = _call(kc.get_evidence, base_url, headers, evidence_id)
    _print_json(data)


@evidence_app.command("facts")
def get_facts_for_evidence(
    evidence_id: str = typer.Argument(..., help="Evidence id (UUID)."),
    table: bool = TABLE_OPTION,
) -> None:
    """List every fact this evidence backs (ingested or retracted by it)."""
    base_url, headers = _client_context()
    data = _call(kc.get_facts_for_evidence, base_url, headers, evidence_id)
    _render_list(data, table=table)


@evidence_app.command("retract")
def retract_evidence(
    evidence_id: str = typer.Argument(..., help="Evidence id (UUID).")
) -> None:
    """Retract evidence (tombstone, never a hard delete)."""
    base_url, headers = _client_context()
    data = _call(kc.retract_evidence, base_url, headers, evidence_id)
    _print_json(data)


# --------------------------------------------------------------------------
# attributes
# --------------------------------------------------------------------------


@attributes_app.command("create")
def create_attribute(
    attribute: str = typer.Argument(
        ..., help="Attribute (predicate) name to register."
    ),
    value_type: ValueType = typer.Option(
        ...,
        "--value-type",
        help="Value type facts using this attribute must have.",
    ),
    cardinality: Cardinality = typer.Option(
        Cardinality.many,
        help="'one' (single active value per entity) or 'many'.",
    ),
    description: Optional[str] = typer.Option(
        None, help="Human-readable description."
    ),
) -> None:
    """Register an attribute (predicate) that facts may reference."""
    base_url, headers = _client_context()
    data = _call(
        kc.create_attribute,
        base_url,
        headers,
        attribute,
        value_type.value,
        cardinality.value,
        description,
    )
    _print_json(data)


@attributes_app.command("list")
def list_attributes(
    include_retracted: bool = typer.Option(
        False, help="Include retracted attributes."
    ),
    table: bool = TABLE_OPTION,
) -> None:
    """List registered attributes."""
    base_url, headers = _client_context()
    data = _call(kc.list_attributes, base_url, headers, include_retracted)
    _render_list(data, table=table)


@attributes_app.command("get")
def get_attribute(
    attribute: str = typer.Argument(..., help="Attribute name.")
) -> None:
    """Get an attribute's schema."""
    base_url, headers = _client_context()
    data = _call(kc.get_attribute, base_url, headers, attribute)
    _print_json(data)


@attributes_app.command("retract")
def retract_attribute(
    attribute: str = typer.Argument(..., help="Attribute name.")
) -> None:
    """Retract an attribute (facts already using it are unaffected)."""
    base_url, headers = _client_context()
    data = _call(kc.retract_attribute, base_url, headers, attribute)
    _print_json(data)


# --------------------------------------------------------------------------
# facts
# --------------------------------------------------------------------------


@facts_app.command("ingest")
def ingest_fact(
    entity: str = typer.Argument(..., help="Entity id this fact is about."),
    attribute: str = typer.Argument(
        ..., help="Registered attribute (predicate) name."
    ),
    value_type: ValueType = typer.Option(
        ...,
        "--value-type",
        help="Must match the attribute's registered value_type.",
    ),
    value: str = typer.Option(
        ...,
        "--value",
        help="The value, as a string (coerced to --value-type).",
    ),
    evidence_id: Optional[str] = typer.Option(
        None, help="Reuse an existing evidence row's id."
    ),
    evidence_url: Optional[str] = typer.Option(
        None, help="Create evidence inline: the source URL."
    ),
    evidence_artifact_path: Optional[str] = typer.Option(
        None,
        help="Create evidence inline: the durable artifact snapshot's URL.",
    ),
    evidence_media_type: Optional[str] = typer.Option(
        None, help="Create evidence inline: optional MIME type."
    ),
) -> None:
    """
    Ingest a fact. Provide either --evidence-id, or both
    --evidence-url and --evidence-artifact-path to create evidence inline
    first. Runs the validation rule engine server-side; a blocking failure
    exits 2 with the failing rules' tags on stderr.
    """
    base_url, headers = _client_context()

    try:
        resolved_evidence_id = kc.resolve_evidence_id(
            base_url,
            headers,
            evidence_id=evidence_id,
            evidence_url=evidence_url,
            artifact_path=evidence_artifact_path,
            media_type=evidence_media_type,
        )
    except ValueError as exc:
        _fail_value_error(exc)
    except kc.KBAPIError as exc:
        _fail_api(exc)
    except httpx.RequestError as exc:
        _fail_connection(exc)

    try:
        coerced_value = kc.coerce_fact_value(value, value_type.value)
    except ValueError as exc:
        _fail_value_error(exc)

    data = _call(
        kc.create_fact,
        base_url,
        headers,
        entity,
        attribute,
        value_type.value,
        coerced_value,
        resolved_evidence_id,
    )
    _print_json(data)


@facts_app.command("get")
def get_fact(
    fact_id: int = typer.Argument(..., help="Numeric fact id.")
) -> None:
    """Get a fact by id, with its full validation/reconciliation trail."""
    base_url, headers = _client_context()
    data = _call(kc.get_fact, base_url, headers, fact_id)
    _print_json(data)


@facts_app.command("retract")
def retract_fact(
    fact_id: int = typer.Argument(..., help="Numeric fact id."),
    retraction_evidence_id: Optional[str] = typer.Option(
        None, help="Evidence (UUID) justifying the retraction."
    ),
) -> None:
    """Retract a fact (tombstone) and re-reconcile its entity/attribute."""
    base_url, headers = _client_context()
    data = _call(
        kc.retract_fact, base_url, headers, fact_id, retraction_evidence_id
    )
    _print_json(data)


@facts_app.command("list")
def list_facts(
    limit: int = typer.Option(200, help="Maximum rows to return."),
    table: bool = TABLE_OPTION,
) -> None:
    """List all currently active facts, most recent first."""
    base_url, headers = _client_context()
    data = _call(kc.list_facts, base_url, headers, limit)
    _render_list(data, table=table)


@facts_app.command("search")
def search_facts(
    q: str = typer.Argument(
        ..., help="Free-text query over string-valued facts."
    ),
    attribute: Optional[str] = typer.Option(
        None, help="Restrict to one attribute."
    ),
    limit: int = typer.Option(20, help="Maximum rows to return."),
    table: bool = TABLE_OPTION,
) -> None:
    """Fuzzy-search facts by (string) attribute value."""
    base_url, headers = _client_context()
    data = _call(kc.search_facts, base_url, headers, q, attribute, limit)
    _render_list(data, table=table, inner_key="fact")


@facts_app.command("for-entity")
def get_facts_for_entity(
    entity: str = typer.Argument(..., help="Entity id."),
    table: bool = TABLE_OPTION,
) -> None:
    """Get the reconciled, currently-active facts for an entity."""
    base_url, headers = _client_context()
    data = _call(kc.get_facts_for_entity, base_url, headers, entity)
    _render_list(data, table=table)


# --------------------------------------------------------------------------
# entities
# --------------------------------------------------------------------------


@entities_app.command("list")
def list_entities(
    limit: int = typer.Option(100, help="Maximum rows to return."),
    table: bool = TABLE_OPTION,
) -> None:
    """List distinct entity ids with at least one active fact."""
    base_url, headers = _client_context()
    data = _call(kc.list_entities, base_url, headers, limit)
    _render_list(data, table=table)


@entities_app.command("search")
def search_entities(
    q: str = typer.Argument(
        ..., help="Free-text query, fuzzy-matched against entity ids."
    ),
    limit: int = typer.Option(20, help="Maximum rows to return."),
    table: bool = TABLE_OPTION,
) -> None:
    """Fuzzy-search entity ids by similar name."""
    base_url, headers = _client_context()
    data = _call(kc.search_entities, base_url, headers, q, limit)
    _render_list(data, table=table)
