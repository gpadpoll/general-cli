# Welcome to gencli

`gencli` is a general-purpose CLI: a library of functions exposed as commands,
built to be used and extended by agents. Every tool is a small set of pure
functions wrapped in a thin Typer command, so the same logic can be called
programmatically (from Python, from a notebook) or from the shell.

See [AGENTS.md](https://github.com/gpadpoll/general-cli/blob/main/AGENTS.md)
in the repository root for the full SDLC agents must follow when adding or
changing a tool.

## Installation

```bash
pip install gencli-agents
```

The PyPI distribution is named `gencli-agents` (the name `gencli` was
already taken by an unrelated project), but the import and the CLI command
are both still `gencli`.

## Usage

The CLI provides a set of commands, one Typer sub-app per tool:

```bash
gencli --help
gencli config --help
gencli example --help
gencli kb --help
```

`gencli example` is the reference implementation new tools are modeled on.
`gencli kb` is a client for an external Knowledge Base HTTP API (a fact
store with evidence and rule-engine audit trails); see its notebook below
and `gencli/kb_client.py` for the full command surface.

## Notebooks

Every tool's pure functions are documented as a Jupyter notebook under
`docs/notebooks/`, demonstrating how to call them directly and how they map
onto the equivalent CLI commands. Notebooks are converted to Markdown for
inclusion in this site. To regenerate the converted docs locally, run:

```bash
make convert-notebooks
```

- **Example notebook**: `notebooks/example.md` — the `example` tool's
  pure functions (`slugify`, `word_count`, `reverse_words`).
- **KB client notebook**: `notebooks/kb.md` — `gencli.kb_client`'s pure
  request/response-shaping functions backing the `kb` command group.

## Development

For developers and agents extending this CLI:

### Testing

Run the test suite:

```bash
pytest tests/ -v
```

### Building Documentation

Build the documentation locally:

```bash
mkdocs serve -f docs/mkdocs.yml
```

The CLI is built with:
- [Typer](https://typer.tiangolo.com/) - Modern CLI framework
- [Rich](https://rich.readthedocs.io/) - Rich text and beautiful formatting
- [Pytest](https://pytest.org/) - Testing framework
