"""
Client library for the external Knowledge Base HTTP API.

The Knowledge Base ("kb") is a separate service: a fact store where every
fact records its evidence and the rule-engine decisions that admitted it
(see its own README for the domain model). This module is the reusable,
importable "SDK" for that API — everything in `gencli/commands/kb.py` is a
thin Typer wrapper around the functions here.

Two layers, per AGENTS.md's functional-programming rules:

1. Pure functions: payload builders, value coercion, and error-shape
   helpers. No I/O, no side effects, deterministic for a given input.
   These are what should be imported and unit-tested directly.
2. I/O boundary functions: auth token minting and the single low-level
   HTTP call (`request`). Every per-endpoint function funnels through
   `request`, so it's the one place CLI-wiring tests need to monkeypatch.
"""

import json as json_module
import os
from typing import Any, Dict, List, Optional

import google.auth.transport.requests
import httpx
from google.oauth2 import id_token, service_account


class KBAPIError(Exception):
    """Raised when the Knowledge Base API returns a non-2xx response."""

    def __init__(self, status_code: int, body: Any, url: str) -> None:
        self.status_code = status_code
        self.body = body
        self.url = url
        super().__init__(f"{status_code} from {url}: {body!r}")


# --------------------------------------------------------------------------
# Pure functions (no I/O, no side effects, deterministic output for a given
# input). See AGENTS.md's functional programming rules.
# --------------------------------------------------------------------------


def build_auth_header(token: Optional[str]) -> Dict[str, str]:
    """Build an ``Authorization`` header dict, or ``{}`` if no token."""
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


def build_evidence_create_payload(
    url: str, artifact_path: str, media_type: Optional[str] = None
) -> Dict[str, Any]:
    """Build the JSON body for ``POST /api/evidence`` (``EvidenceCreate``)."""
    return {
        "url": url,
        "artifact_path": artifact_path,
        "media_type": media_type,
    }


def build_evidence_upload_form(
    url: str, media_type: Optional[str] = None
) -> Dict[str, str]:
    """
    Build the multipart form fields (everything but the file itself) for
    ``POST /api/evidence/upload``.
    """
    form = {"url": url}
    if media_type is not None:
        form["media_type"] = media_type
    return form


def build_attribute_create_payload(
    attribute: str,
    value_type: str,
    cardinality: str = "many",
    description: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the JSON body for ``POST /api/attributes``."""
    return {
        "attribute": attribute,
        "value_type": value_type,
        "cardinality": cardinality,
        "description": description,
    }


_TRUE_STRINGS = {"true", "1", "yes"}
_FALSE_STRINGS = {"false", "0", "no"}
_VALUE_TYPES = {"entity", "string", "number", "boolean", "datetime", "json"}


def coerce_fact_value(raw: str, value_type: str) -> Any:
    """
    Convert a raw CLI string into the JSON-native value ``FactCreate.value``
    expects, based on the target attribute's ``value_type``.

    Raises:
        ValueError: if ``raw`` can't be coerced to ``value_type``, or
            ``value_type`` isn't one of the API's known value types.
        json.JSONDecodeError: if ``value_type`` is ``"json"`` and ``raw``
            isn't valid JSON.
    """
    if value_type not in _VALUE_TYPES:
        raise ValueError(f"unknown value_type {value_type!r}")
    if value_type in ("entity", "string", "datetime"):
        return raw
    if value_type == "number":
        try:
            if "." in raw or "e" in raw.lower():
                return float(raw)
            return int(raw)
        except ValueError:
            raise ValueError(f"'{raw}' is not a valid number") from None
    if value_type == "boolean":
        lowered = raw.strip().lower()
        if lowered in _TRUE_STRINGS:
            return True
        if lowered in _FALSE_STRINGS:
            return False
        raise ValueError(
            f"'{raw}' is not a valid boolean (expected one of "
            f"{sorted(_TRUE_STRINGS | _FALSE_STRINGS)})"
        )
    # value_type == "json"
    return json_module.loads(raw)


def build_fact_create_payload(
    entity: str, attribute: str, value_type: str, value: Any, evidence_id: str
) -> Dict[str, Any]:
    """Build the JSON body for ``POST /api/facts`` (``FactCreate``)."""
    return {
        "entity": entity,
        "attribute": attribute,
        "value_type": value_type,
        "value": value,
        "evidence_id": evidence_id,
    }


def build_retract_fact_payload(
    retraction_evidence_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the JSON body for ``POST /api/facts/{id}/retract``."""
    return {"retraction_evidence_id": retraction_evidence_id}


def validate_evidence_selector(
    evidence_id: Optional[str],
    evidence_url: Optional[str],
    artifact_path: Optional[str],
) -> None:
    """
    Validate that exactly one evidence-selection strategy was given: an
    existing ``evidence_id``, or an ``(evidence_url, artifact_path)`` pair
    to create evidence inline.

    Raises:
        ValueError: if the selector is ambiguous (both given), incomplete
            (neither given, or only one of the inline pair given).
    """
    has_id = evidence_id is not None
    has_inline = evidence_url is not None or artifact_path is not None
    if has_id and has_inline:
        raise ValueError(
            "pass either --evidence-id or "
            "--evidence-url/--evidence-artifact-path, not both"
        )
    if not has_id and not has_inline:
        raise ValueError(
            "pass either --evidence-id or "
            "--evidence-url/--evidence-artifact-path"
        )
    if has_inline and (evidence_url is None or artifact_path is None):
        raise ValueError(
            "--evidence-url and --evidence-artifact-path must be given "
            "together"
        )


def resolve_api_audience(base_url: str, api_audience: Optional[str]) -> str:
    """The ID-token audience: an explicit override, or ``base_url`` itself."""
    return api_audience or base_url


def is_validation_failure(status_code: int, body: Any) -> bool:
    """
    True iff this is the special 422 shape from a blocked ``POST /api/facts``:
    ``{"detail": {"validation_tags": [...]}}``. Every other error shape uses
    a plain string ``detail``.
    """
    return (
        status_code == 422
        and isinstance(body, dict)
        and isinstance(body.get("detail"), dict)
        and "validation_tags" in body["detail"]
    )


def extract_validation_tags(body: Any) -> List[Dict[str, Any]]:
    """Pull the ``validation_tags`` list out of a blocked-ingest 422 body."""
    tags: List[Dict[str, Any]] = body["detail"]["validation_tags"]
    return tags


def extract_error_message(status_code: int, body: Any) -> str:
    """
    A human-readable message for any error body: the plain-string ``detail``
    FastAPI normally sends, or a string fallback for anything else (an
    object ``detail``, a non-JSON body, an unhandled 5xx).
    """
    if isinstance(body, dict) and isinstance(body.get("detail"), str):
        return body["detail"]
    return str(body)


def flatten_search_result(
    item: Dict[str, Any], inner_key: str
) -> Dict[str, Any]:
    """
    Flatten a ``{<inner_key>: {...}, "score": float}`` search result (the
    shape of ``EvidenceSearchResult``/``FactSearchResult``) into one dict,
    for table rendering.
    """
    inner = item[inner_key]
    return {**inner, "score": item["score"]}


# --------------------------------------------------------------------------
# I/O boundary: auth token minting and the single low-level HTTP call.
# Everything above this line is pure; everything below performs real I/O.
# --------------------------------------------------------------------------


def mint_service_account_id_token(
    service_account_file: str, audience: str
) -> str:
    """
    Mint a fresh Google-signed ID token for the given service-account key
    file, audienced to ``audience`` (the Knowledge Base API's own base
    URL) — the same mechanism the kb repo's own CI smoke tests use to
    authenticate as a machine client. Not cached to disk; call this fresh
    each time a token is needed.
    """
    credentials = service_account.IDTokenCredentials.from_service_account_file(
        service_account_file, target_audience=audience
    )
    credentials.refresh(google.auth.transport.requests.Request())
    token: str = credentials.token
    return token


def mint_adc_id_token(audience: str) -> str:
    """
    Mint a Google-signed ID token for ``audience`` from Application
    Default Credentials: the metadata server when running on Cloud Run /
    GCE / GKE (the runtime service account — no key file exists there), or
    the key file named by ``GOOGLE_APPLICATION_CREDENTIALS`` locally.
    """
    token: str = id_token.fetch_id_token(
        google.auth.transport.requests.Request(), audience
    )
    return token


def build_headers_for_config(config: Dict[str, Any]) -> Dict[str, str]:
    """
    Build the ``Authorization`` header (or no header at all) for the
    configured ``kb_auth_mode``:

    - ``"none"``: no header — for a local server running with its own
      auth disabled.
    - ``"token"``: a raw bearer token from config (``kb_token``).
    - ``"service_account"``: mint a fresh ID token from a service-account
      key file (``kb_service_account_file``), audienced to
      ``kb_api_audience`` or ``kb_base_url``.
    - ``"adc"``: mint a fresh ID token from Application Default
      Credentials (the runtime service account on Cloud Run), same
      audience rule. For services that call the KB without a key file.

    Raises:
        ValueError: for an unknown ``kb_auth_mode``, or ``"service_account"``
            mode with no ``kb_service_account_file`` configured.
    """
    mode = config.get("kb_auth_mode", "none")
    if mode == "none":
        return {}
    if mode == "token":
        return build_auth_header(config.get("kb_token"))
    if mode == "service_account":
        service_account_file = config.get("kb_service_account_file")
        if not service_account_file:
            raise ValueError(
                "kb_auth_mode is 'service_account' but "
                "kb_service_account_file "
                "is not set (gencli config set kb_service_account_file <path>)"
            )
        audience = resolve_api_audience(
            config.get("kb_base_url") or "", config.get("kb_api_audience")
        )
        token = mint_service_account_id_token(service_account_file, audience)
        return build_auth_header(token)
    if mode == "adc":
        audience = resolve_api_audience(
            config.get("kb_base_url") or "", config.get("kb_api_audience")
        )
        return build_auth_header(mint_adc_id_token(audience))
    raise ValueError(f"unknown kb_auth_mode {mode!r}")


def request(
    method: str,
    path: str,
    *,
    base_url: str,
    headers: Optional[Dict[str, str]] = None,
    params: Optional[Dict[str, Any]] = None,
    json_body: Optional[Dict[str, Any]] = None,
    data: Optional[Dict[str, Any]] = None,
    files: Optional[Dict[str, Any]] = None,
    timeout: float = 30.0,
) -> Any:
    """
    The single low-level HTTP call every function in this module funnels
    through — the one function CLI-wiring tests monkeypatch.

    ``data``/``files`` are for multipart requests (e.g. evidence upload);
    don't set a ``Content-Type`` in ``headers`` when passing ``files`` —
    httpx sets the multipart boundary itself.

    Returns the parsed JSON response body (or raw text if the response
    isn't JSON).

    Raises:
        KBAPIError: on a non-2xx response.
        httpx.RequestError: on a transport-level failure (DNS, connection,
            timeout) — there was no HTTP response at all.
    """
    response = httpx.request(
        method,
        f"{base_url.rstrip('/')}{path}",
        headers=headers,
        params=params,
        json=json_body,
        data=data,
        files=files,
        timeout=timeout,
    )
    try:
        body = response.json()
    except ValueError:
        body = response.text
    if response.is_error:
        raise KBAPIError(response.status_code, body, str(response.url))
    return body


# --------------------------------------------------------------------------
# Per-endpoint functions: thin, each builds its params/payload (via a pure
# function above, where one exists) and calls request().
# --------------------------------------------------------------------------


def me(base_url: str, headers: Dict[str, str]) -> Dict[str, Any]:
    return request("GET", "/api/me", base_url=base_url, headers=headers)


def create_evidence(
    base_url: str,
    headers: Dict[str, str],
    url: str,
    artifact_path: str,
    media_type: Optional[str] = None,
) -> Dict[str, Any]:
    payload = build_evidence_create_payload(url, artifact_path, media_type)
    return request(
        "POST",
        "/api/evidence",
        base_url=base_url,
        headers=headers,
        json_body=payload,
    )


def upload_evidence(
    base_url: str,
    headers: Dict[str, str],
    url: str,
    file_path: str,
    media_type: Optional[str] = None,
    filename: Optional[str] = None,
    timeout: float = 60.0,
) -> Dict[str, Any]:
    """
    ``POST /api/evidence/upload`` — uploads ``file_path``'s bytes as the
    artifact and registers evidence in one call.
    """
    form = build_evidence_upload_form(url, media_type)
    name = filename or os.path.basename(file_path)
    with open(file_path, "rb") as fh:
        files = {"file": (name, fh, media_type or "application/octet-stream")}
        return request(
            "POST",
            "/api/evidence/upload",
            base_url=base_url,
            headers=headers,
            data=form,
            files=files,
            timeout=timeout,
        )


def list_evidence(
    base_url: str,
    headers: Dict[str, str],
    include_retracted: bool = False,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    params = {"include_retracted": include_retracted, "limit": limit}
    return request(
        "GET",
        "/api/evidence",
        base_url=base_url,
        headers=headers,
        params=params,
    )


def search_evidence(
    base_url: str, headers: Dict[str, str], q: str, limit: int = 20
) -> List[Dict[str, Any]]:
    params = {"q": q, "limit": limit}
    return request(
        "GET",
        "/api/evidence/search",
        base_url=base_url,
        headers=headers,
        params=params,
    )


def get_evidence(
    base_url: str, headers: Dict[str, str], evidence_id: str
) -> Dict[str, Any]:
    return request(
        "GET",
        f"/api/evidence/{evidence_id}",
        base_url=base_url,
        headers=headers,
    )


def get_facts_for_evidence(
    base_url: str, headers: Dict[str, str], evidence_id: str
) -> List[Dict[str, Any]]:
    return request(
        "GET",
        f"/api/evidence/{evidence_id}/facts",
        base_url=base_url,
        headers=headers,
    )


def retract_evidence(
    base_url: str, headers: Dict[str, str], evidence_id: str
) -> Dict[str, Any]:
    return request(
        "POST",
        f"/api/evidence/{evidence_id}/retract",
        base_url=base_url,
        headers=headers,
    )


def create_attribute(
    base_url: str,
    headers: Dict[str, str],
    attribute: str,
    value_type: str,
    cardinality: str = "many",
    description: Optional[str] = None,
) -> Dict[str, Any]:
    payload = build_attribute_create_payload(
        attribute, value_type, cardinality, description
    )
    return request(
        "POST",
        "/api/attributes",
        base_url=base_url,
        headers=headers,
        json_body=payload,
    )


def list_attributes(
    base_url: str, headers: Dict[str, str], include_retracted: bool = False
) -> List[Dict[str, Any]]:
    params = {"include_retracted": include_retracted}
    return request(
        "GET",
        "/api/attributes",
        base_url=base_url,
        headers=headers,
        params=params,
    )


def get_attribute(
    base_url: str, headers: Dict[str, str], attribute: str
) -> Dict[str, Any]:
    return request(
        "GET",
        f"/api/attributes/{attribute}",
        base_url=base_url,
        headers=headers,
    )


def retract_attribute(
    base_url: str, headers: Dict[str, str], attribute: str
) -> Dict[str, Any]:
    return request(
        "POST",
        f"/api/attributes/{attribute}/retract",
        base_url=base_url,
        headers=headers,
    )


def create_fact(
    base_url: str,
    headers: Dict[str, str],
    entity: str,
    attribute: str,
    value_type: str,
    value: Any,
    evidence_id: str,
) -> Dict[str, Any]:
    payload = build_fact_create_payload(
        entity, attribute, value_type, value, evidence_id
    )
    return request(
        "POST",
        "/api/facts",
        base_url=base_url,
        headers=headers,
        json_body=payload,
    )


def get_fact(
    base_url: str, headers: Dict[str, str], fact_id: int
) -> Dict[str, Any]:
    return request(
        "GET", f"/api/facts/{fact_id}", base_url=base_url, headers=headers
    )


def retract_fact(
    base_url: str,
    headers: Dict[str, str],
    fact_id: int,
    retraction_evidence_id: Optional[str] = None,
) -> Dict[str, Any]:
    payload = build_retract_fact_payload(retraction_evidence_id)
    return request(
        "POST",
        f"/api/facts/{fact_id}/retract",
        base_url=base_url,
        headers=headers,
        json_body=payload,
    )


def list_entities(
    base_url: str, headers: Dict[str, str], limit: int = 100
) -> List[str]:
    params = {"limit": limit}
    return request(
        "GET",
        "/api/entities",
        base_url=base_url,
        headers=headers,
        params=params,
    )


def search_entities(
    base_url: str, headers: Dict[str, str], q: str, limit: int = 20
) -> List[Dict[str, Any]]:
    params = {"q": q, "limit": limit}
    return request(
        "GET",
        "/api/entities/search",
        base_url=base_url,
        headers=headers,
        params=params,
    )


def list_facts(
    base_url: str, headers: Dict[str, str], limit: int = 200
) -> List[Dict[str, Any]]:
    params = {"limit": limit}
    return request(
        "GET", "/api/facts", base_url=base_url, headers=headers, params=params
    )


def search_facts(
    base_url: str,
    headers: Dict[str, str],
    q: str,
    attribute: Optional[str] = None,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    params: Dict[str, Any] = {"q": q, "limit": limit}
    if attribute is not None:
        params["attribute"] = attribute
    return request(
        "GET",
        "/api/facts/search",
        base_url=base_url,
        headers=headers,
        params=params,
    )


def get_facts_for_entity(
    base_url: str, headers: Dict[str, str], entity: str
) -> List[Dict[str, Any]]:
    return request(
        "GET", f"/api/facts/{entity}", base_url=base_url, headers=headers
    )


def resolve_evidence_id(
    base_url: str,
    headers: Dict[str, str],
    *,
    evidence_id: Optional[str] = None,
    evidence_url: Optional[str] = None,
    artifact_path: Optional[str] = None,
    media_type: Optional[str] = None,
) -> str:
    """
    Return an evidence id to use for a fact: ``evidence_id`` untouched if
    given, or the id of a freshly created evidence row when
    ``evidence_url``/``artifact_path`` are given instead (mirrors the kb
    frontend's own ingest convenience). Validates the selector first, so a
    bad combination never reaches the network.
    """
    validate_evidence_selector(evidence_id, evidence_url, artifact_path)
    if evidence_id is not None:
        return evidence_id
    assert (
        evidence_url is not None and artifact_path is not None
    )  # validated above
    created = create_evidence(
        base_url, headers, evidence_url, artifact_path, media_type
    )
    created_id: str = created["id"]
    return created_id
