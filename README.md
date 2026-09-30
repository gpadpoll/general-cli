# gencli

A general-purpose CLI: a library of functions exposed as commands, built for
agents to use and extend. Every tool is implemented as a small set of pure
functions with a thin Typer command wrapper, so the same logic works both as
a shell command and as a plain Python import (from a script, a notebook, or
another agent's tool).

See [`AGENTS.md`](./AGENTS.md) for the SDLC every new tool must follow
(pure functions, tests, and a documentation notebook per feature).

## Get Started

See the Docs page: https://gpadpoll.github.io/general-cli/

## Important: Poetry Version

To avoid compatibility errors (such as TypeError related to canonicalize_version), ensure you are using an up-to-date version of Poetry:

   ```bash
   pip install --upgrade poetry
   ```

If you encounter installation issues, upgrading Poetry usually resolves them.

## Quick Start

1. **Environment**: create (or verify) the conda environment for this project
   ```bash
   make conda-env
   ```

2. **Installation**: install the package in development mode
   ```bash
   make install
   ```

3. **Basic Usage**: try the built-in commands
   ```bash
   gencli config list
   gencli config set theme dark
   gencli config get theme

   gencli example slug "Hello, World!"
   gencli example word-count "to be or not to be"
   gencli example reverse "the quick brown fox"
   ```

## CLI Commands

- `gencli config` — manage CLI configuration (`set`, `get`, `list`, `reset`),
  stored as JSON at `~/.gencli_config.json` (override with
  `GENCLI_CONFIG_PATH`).
- `gencli example` — reference tool (`slug`, `word-count`, `reverse`). This
  is the template to copy when adding a new tool: see
  `gencli/commands/example.py`, its tests in `tests/test_example.py`, and
  its notebook in `docs/notebooks/example.ipynb`.
- `gencli kb` — client for an external Knowledge Base HTTP API (a fact
  store where every fact records its evidence and the rule-engine
  decisions that admitted it): `me`, `evidence`, `attributes`, `facts`,
  `entities`. JSON output by default (this CLI's audience is agents); add
  `--table` on list/search commands for a human-readable view. Configure
  with:
  ```bash
  gencli config set kb_base_url http://localhost:8000
  gencli config set kb_auth_mode none            # or "token" / "service_account" / "adc"
  gencli config set kb_token <bearer-token>            # for kb_auth_mode=token
  gencli config set kb_service_account_file <path>     # for kb_auth_mode=service_account
  ```
  Example usage:
  ```bash
  gencli kb me
  gencli kb attributes create shoe_size --value-type number
  gencli kb facts ingest alice shoe_size --value-type number --value 8 \
      --evidence-url https://example.com --evidence-artifact-path gs://bucket/a.png
  gencli kb facts for-entity alice --table
  ```
  See `gencli/kb_client.py` (the pure/importable "SDK") and
  `docs/notebooks/kb.ipynb`.
- `gencli crawl` — client for a local [crawl4ai](https://github.com/unclecode/crawl4ai)
  server: `fetch` (one page's clean markdown), `deep` (BFS domain crawl),
  `screenshot`, `health`. crawl4ai's free/local library has no general web
  search — only crawling of URLs you already know; the agent brings its
  own URLs. Every command accepts `--create-evidence` to upload the
  crawled content as KB evidence and include it in the output in one
  call — the agent then reads the content, decides what facts are in it,
  and ingests them with the `gencli kb` commands above; `gencli crawl`
  itself never does fact extraction. Configure with:
  ```bash
  gencli config set crawl_base_url http://localhost:11235
  gencli config set crawl_api_token <token>   # matches CRAWL4AI_API_TOKEN
  gencli config set crawl_cache_dir ~/.gencli_cache/crawl
  ```
  Example usage:
  ```bash
  docker run -d -p 11235:11235 -e CRAWL4AI_API_TOKEN=<token> unclecode/crawl4ai:latest
  gencli crawl health
  gencli crawl fetch https://example.com --create-evidence
  gencli crawl deep https://example.com --max-pages 20 --create-evidence
  ```
  See `gencli/crawl_client.py` (the pure/importable "SDK") and
  `docs/notebooks/crawl.ipynb`.

- `gencli agent` — client for an
  [agent-service](https://github.com/gpadpoll/agent-service) API: LangGraph
  agents behind a LiteLLM gateway (any LLM), whose tools are this CLI's own
  `kb` and `crawl` clients. `list`, `invoke`, `stream`, `thread`. Configure
  with:
  ```bash
  gencli config set agent_base_url http://localhost:8080
  gencli config set agent_api_key <key>
  ```
  Example usage:
  ```bash
  gencli agent invoke general "What do we know about alice?" --thread t1
  gencli agent invoke crawl_to_facts "Store the facts from https://example.com" -o
  gencli agent stream general "Summarize alice" --model smart
  ```
  See `gencli/agent_client.py` and `docs/notebooks/agent.ipynb`. For
  services calling the KB from Cloud Run (no key file), use
  `kb_auth_mode adc` (ID tokens from Application Default Credentials).

## Adding a new tool

1. Create `gencli/commands/<tool>.py`. Implement the tool's logic as pure
   functions (no I/O, no side effects, deterministic), and thin Typer
   commands that call them.
2. Register the sub-app in `gencli/main.py`.
3. Write unit tests in `tests/test_<tool>.py`: direct tests for the pure
   functions, plus a couple of `CliRunner` tests for the CLI wiring.
4. Document the pure functions as a Jupyter notebook in
   `docs/notebooks/<tool>.ipynb` (copy `docs/notebooks/example.ipynb` as a
   starting point) and list it in `docs/docs/index.md`.
5. Run `make format`, `make check`, `make test`, and the pre-commit hooks
   before opening a PR.

Full guidelines: [`AGENTS.md`](./AGENTS.md).

## Development

### Prerequisites

This project uses [Poetry](https://python-poetry.org/) for dependency management, inside a dedicated conda environment.

```bash
make conda-env   # create/verify the conda env and install dependencies
```

### Setup

1. **Install dependencies**:
   ```bash
   make install
   ```
   This installs the package and all development dependencies using Poetry.

2. **Install pre-commit hooks**:
   ```bash
   make pre-commit
   ```

### Testing

Run the comprehensive test suite:

```bash
make test
```

Or run tests directly with Poetry:

```bash
poetry run pytest -vvv
```

### Documentation

1. **Install docs dependencies**:
   ```bash
   make docs
   ```

2. **Serve docs locally**:
   ```bash
   make serve-docs
   ```
   Or run directly with Poetry:
   ```bash
   poetry run mkdocs serve -f docs/mkdocs.yml
   ```

3. **View documentation**: Open http://localhost:8000

### Code Quality

- **Format code**: `make format` or `poetry run black .`
- **Check formatting**: `make check` or `poetry run black --check --diff .`
- **Run linting**: `poetry run flake8`
- **Type checking**: `poetry run mypy .`
- **Clean artifacts**: `make clean`

### Docker Testing

Test the CLI in a clean container environment:

1. **Build image**:
   ```bash
   make docker-image
   ```

2. **Run commands**:
   ```bash
   docker run --rm gencli --help
   docker run --rm gencli config list
   docker run --rm gencli example slug "Hello, World!"
   ```

## Configuration Storage

- **Default location**: `~/.gencli_config.json`
- **Custom location**: Set `GENCLI_CONFIG_PATH` environment variable
- **Format**: JSON with automatic type preservation
- **Default values**: Includes theme, output_format, auto_save, and debug settings

## Distribution

### PyPI Publishing (automated, recommended)

`.github/workflows/publish.yml` builds and publishes to PyPI whenever a
GitHub Release is published (or the workflow is run manually). It uses
[PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/) (OIDC)
— there is no API token to generate or store as a secret.

The PyPI distribution name is `gencli-agents` — the plain name `gencli`
is already taken by an unrelated project (`gen-cli`, which normalizes to
the same name once PyPI strips hyphens). The import name and CLI command
are unaffected and remain `gencli`.

**One-time setup** (do this before the first release):

1. On PyPI, go to "Add a new pending publisher"
   (https://pypi.org/manage/account/publishing/, and the equivalent on
   https://test.pypi.org/manage/account/publishing/ if you want to dry-run
   via TestPyPI first) and add a trusted publisher with:
   - PyPI Project Name: `gencli-agents`
   - Owner: `gpadpoll`
   - Repository: `general-cli`
   - Workflow file: `publish.yml`
   - Environment name: `pypi` (or `testpypi` on test.pypi.org)
2. The `pypi` and `testpypi` GitHub environments already exist on the repo
   (Settings → Environments) — created for this workflow; add required
   reviewers there if you want a manual approval gate before a publish
   runs.

**To cut a release:**

1. Bump `version` in `pyproject.toml`.
2. Commit, then tag: `git tag vX.Y.Z && git push --tags`.
3. Create a GitHub Release from that tag. This triggers the workflow,
   which verifies the tag matches the `pyproject.toml` version, builds the
   sdist/wheel, and publishes them to PyPI.

### PyPI Publishing (manual)

> **NOTE**: Ensure you have a [PyPI account](https://pypi.org/account/register/) before publishing.

1. **Create distributions**:
   ```bash
   make distributions
   ```
   This builds the package using Poetry.

2. **Upload to PyPI**:
   ```bash
   poetry publish
   ```
   Or use twine:
   ```bash
   twine upload dist/*
   ```

### Project layout

```text
.
├── AGENTS.md                # SDLC guidelines for agents adding/changing tools
├── Dockerfile                # container image build steps
├── Makefile                  # convenience commands (install, test, docs, conda-env, etc.)
├── pyproject.toml            # project metadata and dependencies (Poetry)
├── README.md                 # this file
├── .github/workflows/
│   ├── docs.yml               # builds+deploys the MkDocs site to GitHub Pages
│   └── publish.yml            # builds+publishes to PyPI on GitHub Release
├── scripts/
│   └── setup_conda_env.sh    # creates/verifies the conda environment
├── docs/                     # MkDocs site and notebook resources
│   ├── mkdocs.yml
│   ├── docs/
│   │   └── index.md
│   └── notebooks/
│       ├── example.ipynb     # documents the `example` tool's pure functions
│       ├── kb.ipynb           # documents kb_client's pure functions
│       └── crawl.ipynb         # documents crawl_client's pure functions
├── gencli/                   # main package code
│   ├── __init__.py
│   ├── constants.py
│   ├── main.py                # top-level Typer app, registers command modules
│   ├── utils.py                # shared pure helpers
│   ├── kb_client.py            # KB API "SDK": pure functions + thin HTTP/auth I/O
│   ├── crawl_client.py         # crawl4ai "SDK": pure functions + thin HTTP I/O
│   └── commands/               # one module per tool (Typer sub-app)
│       ├── __init__.py
│       ├── config.py           # configuration management commands
│       ├── example.py          # reference tool: pure functions + CLI wrapper
│       ├── kb.py                # thin Typer wrappers around kb_client
│       └── crawl.py             # thin Typer wrappers around crawl_client
└── tests/
    ├── test_config.py
    ├── test_example.py
    ├── test_kb_client.py
    ├── test_kb.py
    ├── test_crawl_client.py
    └── test_crawl.py
```

## Architecture

Built with modern Python CLI best practices:

- **[Poetry](https://python-poetry.org/)** - Modern dependency management
- **[Typer](https://typer.tiangolo.com/)** - Type-based CLI framework
- **[Rich](https://rich.readthedocs.io/)** - Beautiful terminal output
- **[httpx](https://www.python-httpx.org/)** - HTTP client (`gencli kb`)
- **[google-auth](https://google-auth.readthedocs.io/)** - Google ID token
  minting for `gencli kb`'s service-account auth mode
- **[Pytest](https://pytest.org/)** - Reliable testing framework
- **[MkDocs](https://mkdocs.org/)** - Professional documentation
- **[Black](https://black.readthedocs.io/)** - Code formatting
- **[Pre-commit](https://pre-commit.com/)** - Git hooks for quality

## Help

View all available make commands:

```bash
make help
```

Get CLI help:

```bash
gencli --help
gencli config --help
gencli example --help
gencli kb --help
gencli crawl --help
```
