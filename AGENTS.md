# AGENTS.md

Guidelines for any agent (or human) adding to or modifying `gencli`.

## What this project is

`gencli` is a general-purpose CLI: a **library of functions exposed as
commands**. It is not built around one domain — it's a growing toolbox that
agents extend, one tool at a time, whenever they need a new capability that
is worth keeping as a reusable, testable, documented function.

Two consequences follow from that:

- Every tool must work equally well called from Python (a script, a
  notebook, another agent's code) and from the shell. The CLI layer is
  just one interface onto the function library, not the thing itself.
- The bar for adding a tool is "this logic is reusable and worth naming,"
  not "this is a one-off script." One-off scripts belong in the caller,
  not in this repo.

## Repository layout

```text
gencli/
├── __init__.py         # package-level exports
├── constants.py         # shared constants
├── main.py              # Typer app; registers each command module
├── utils.py             # shared pure helpers used across command modules
└── commands/
    ├── config.py         # built-in: CLI configuration management
    └── example.py         # reference implementation — read this first
tests/
    ├── test_config.py
    └── test_example.py
docs/
├── mkdocs.yml
├── docs/index.md
└── notebooks/
    └── example.ipynb      # documents commands/example.py's pure functions
```

`gencli/commands/example.py`, `tests/test_example.py`, and
`docs/notebooks/example.ipynb` together are the reference implementation.
When in doubt about how to structure a new tool, copy that pattern.

## The SDLC for adding or changing a tool

Follow these steps, in order, for every new function or feature:

1. **Design as pure functions first.** Write the logic as one or more pure
   functions (see "Functional programming rules" below) in
   `gencli/commands/<tool>.py` (new module) or an existing module if the
   function clearly belongs there. Do this before writing any Typer code.
2. **Wrap with a thin CLI command.** Add a Typer command in the same module
   that parses arguments/options, calls the pure function(s), and prints or
   writes the result. The command function itself should contain no
   business logic — only argument handling and the unavoidable I/O.
3. **Register the sub-app.** If it's a new module, `app.add_typer(...)` it
   in `gencli/main.py`.
4. **Write tests.**
   - Unit test every pure function directly, with plain `assert`s, covering
     the normal case, an edge case (empty input, boundary value), and any
     documented error condition.
   - Add a small number of `typer.testing.CliRunner` tests to confirm the
     command is wired correctly (help text, exit codes, basic output) — the
     CLI tests should not re-test logic already covered by the pure
     function tests.
5. **Document as a notebook.** Add `docs/notebooks/<tool>.ipynb` that
   imports and calls the pure functions directly (not through the CLI),
   shows expected output, and notes the equivalent CLI command for each
   example. This notebook is the source of truth for the tool's docs page.
   List it under "Notebooks" in `docs/docs/index.md`.
6. **Convert and check docs.** Run `make convert-notebooks` (or `make
   docs`) and confirm the generated Markdown under `docs/docs/notebooks/`
   reads correctly.
7. **Quality gates before committing:**
   ```bash
   make format   # black
   make check    # black --check --diff
   make test     # pytest
   poetry run flake8
   poetry run mypy .
   ```
   and make sure pre-commit hooks pass (`make pre-commit` installs them
   once; they then run on every commit).
8. **Commit and open a PR.** Use a short, descriptive commit message.
   Explain in the PR description what the tool does, why it's a pure
   function (not a script), and link the notebook.

Never skip steps 4 and 5. A function without tests or a documentation
notebook is not considered part of the library — treat it as unfinished.

## Functional programming rules

Agents must prefer pure functions. Concretely:

- **No side effects in logic functions.** A function that computes
  something should not also print, log, write files, mutate a global, or
  mutate its arguments. If a computation needs a side effect (reading a
  file, calling an API, writing output), split it: a pure function that
  takes already-loaded data and returns a result, plus a thin I/O
  wrapper that does the reading/writing and calls the pure function.
- **Deterministic and total.** Same input → same output, every time. Avoid
  hidden dependence on wall-clock time, environment variables, random
  state, or global mutable state inside the logic layer. If a function
  genuinely needs "now" or an external input, pass it in as a parameter
  (e.g. `def run(data, *, now: datetime)`), don't reach for `datetime.now()`
  inside the pure function.
- **Immutability by default.** Don't mutate input arguments (lists, dicts,
  dataframes) in place; return a new value. Treat function inputs as
  read-only.
- **Small composable functions.** Prefer several small functions that
  compose (see `docs/notebooks/example.ipynb`'s composition example) over
  one large function that does everything.
- **Where I/O is unavoidable** (the Typer command bodies, config
  read/write, network calls), keep it at the boundary: thin, obviously
  correct, and not unit tested for logic (only for wiring). All real logic
  should be extracted into a pure function that sits next to it and is
  imported by it.
- **Prefer plain data.** Return built-in types (`dict`, `list`, dataclasses
  / `NamedTuple`) from pure functions rather than custom stateful objects,
  so results are trivially inspectable, serializable, and testable.

This is "prefer," not "purity at all costs": a config-loading function that
reads a JSON file is inherently impure — that's fine, but keep such
functions minimal and push everything they don't strictly need to do (e.g.
type conversion, validation, merging) into pure helper functions they call.

## Environment

Use the provided conda environment rather than an ad hoc virtualenv or the
system Python:

```bash
make conda-env
```

This runs `scripts/setup_conda_env.sh`, which creates the `gencli` conda
environment if it doesn't exist (or verifies it if it does) and installs
the project into it via Poetry. See that script's header comment for the
environment variables that customize the env name / Python version.

## Dependencies

Add new runtime dependencies to `pyproject.toml` deliberately, one at a
time, only when a tool actually needs them — don't bulk-add libraries
"just in case." Dev/docs/test tooling (black, flake8, mypy, pytest,
mkdocs, jupyter/nbconvert) is already configured; reuse it rather than
introducing alternatives.

## Naming and scope

- CLI command names use kebab-case (`word-count`), Python functions use
  snake_case (`word_count`).
- Config keys and environment variables use the `GENCLI_` prefix
  (`GENCLI_CONFIG_PATH`).
- Keep each command module focused on one tool/domain. If a module grows
  past a handful of unrelated commands, split it.
