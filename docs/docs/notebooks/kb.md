# `kb`: client for the external Knowledge Base API

`gencli kb` is a full CLI client for a separate service, the Knowledge
Base ("kb"): a fact store where every fact records its evidence and the
rule-engine decisions that admitted it.

As with `example.ipynb`, this notebook demonstrates `gencli.kb_client`'s
**pure** functions directly — no live network calls at doc-build time,
since network I/O doesn't belong in a docs build. Each section notes the
equivalent CLI command. A final, non-executed section walks through a
real session against a local `kb` instance.


```python
%load_ext autoreload
%autoreload 2
```

## Building request payloads

Pure functions: given the same arguments, they always build the same
JSON body — no network call, no side effect.


```python
from gencli.kb_client import (
    build_evidence_create_payload,
    build_attribute_create_payload,
)

build_evidence_create_payload(
    "https://example.com/toy-dataset", "gs://kb-evidence/toy-seed.json"
)
```


```python
build_attribute_create_payload("shoe_size", "number", cardinality="one")
```

Equivalent CLI commands:

```bash
gencli kb evidence create https://example.com/toy-dataset gs://kb-evidence/toy-seed.json
gencli kb attributes create shoe_size --value-type number --cardinality one
```

## `coerce_fact_value`

The CLI always receives `--value` as a plain string; this pure function
converts it into the JSON-native value the API's `value_type` expects.


```python
from gencli.kb_client import coerce_fact_value

(
    coerce_fact_value("8", "number"),
    coerce_fact_value("true", "boolean"),
    coerce_fact_value('{"nested": 1}', "json"),
    coerce_fact_value("acme-corp", "entity"),
)
```

Equivalent CLI command:

```bash
gencli kb facts ingest alice shoe_size --value-type number --value 8 --evidence-id <id>
```

## `validate_evidence_selector`

`kb facts ingest` accepts either an existing `--evidence-id`, or
`--evidence-url`/`--evidence-artifact-path` to create evidence inline.
This pure function enforces exactly one of those two forms — it raises
`ValueError` for anything else, before any network call is made.


```python
from gencli.kb_client import validate_evidence_selector

validate_evidence_selector("existing-evidence-id", None, None)  # ok
validate_evidence_selector(None, "https://x", "gs://y")  # ok

try:
    validate_evidence_selector("existing-evidence-id", "https://x", "gs://y")
except ValueError as exc:
    print("rejected:", exc)

try:
    validate_evidence_selector(None, None, None)
except ValueError as exc:
    print("rejected:", exc)
```

The two accepted CLI forms:

```bash
gencli kb facts ingest alice shoe_size --value-type number --value 8 \
    --evidence-id existing-evidence-id

gencli kb facts ingest alice shoe_size --value-type number --value 8 \
    --evidence-url https://x --evidence-artifact-path gs://y
```

## Recognizing a blocked ingest

A `POST /api/facts` blocked by the validation rule engine returns **422**
with a body shaped like `{"detail": {"validation_tags": [...]}}` — an
*object* `detail`, unlike every other error (`{"detail": "a string"}`).
`is_validation_failure`/`extract_validation_tags` recognize this exact
shape.


```python
from gencli.kb_client import is_validation_failure, extract_validation_tags

blocked_body = {
    "detail": {
        "validation_tags": [
            {
                "rule_name": "attribute_known",
                "severity": "blocking",
                "passed": False,
                "reason": 'attribute "not_a_real_attribute" is not registered',
            }
        ]
    }
}

print(is_validation_failure(422, blocked_body))
print(is_validation_failure(404, {"detail": "not found"}))
extract_validation_tags(blocked_body)
```

`gencli kb facts ingest` exits with code **2** (not the generic 1) on
exactly this shape, with the failing tags printed as JSON on stderr:

```bash
gencli kb facts ingest alice not_a_real_attribute --value-type string \
    --value x --evidence-id <id>
echo $?  # 2
```

## `flatten_search_result`

Search endpoints wrap each hit as `{<resource>: {...}, "score": float}`.
This flattens one hit into a single row, for `--table` rendering.


```python
from gencli.kb_client import flatten_search_result

hit = {"evidence": {"id": "e1", "url": "https://example.com"}, "score": 0.91}
flatten_search_result(hit, "evidence")
```

Equivalent CLI command:

```bash
gencli kb evidence search example.com --table
```

## Talking to a real KB instance

Not executed here (this is a docs build, not a live environment) — a
walkthrough against a local `kb` checkout with its dev auth disabled
(`make -C backend up migrate run`, `make -C backend seed`):

```bash
# One-time config: point gencli at the local API, no auth needed locally.
gencli config set kb_base_url http://localhost:8000
gencli config set kb_auth_mode none

# Sanity check + read seeded demo data.
gencli kb me
gencli kb entities list
gencli kb facts for-entity alice --table

# Register a new attribute, then ingest a fact for it, creating evidence
# inline.
gencli kb attributes create shoe_size --value-type number
gencli kb facts ingest alice shoe_size --value-type number --value 8 \
    --evidence-url https://example.com/shoe-size-source \
    --evidence-artifact-path gs://kb-evidence/shoe-size.png

# Inspect the full validation/reconciliation audit trail for that fact.
gencli kb facts get <fact-id-from-the-ingest-response>
```

Against a deployed instance instead of localhost, set
`kb_auth_mode=service_account` and `kb_service_account_file` (or
`kb_auth_mode=token` with a token from `gcloud auth print-identity-token
--audiences=<api-base-url>`) — see `gencli/kb_client.py`'s
`build_headers_for_config` and the project README.
