# Example tool: text utilities

This notebook is the documentation template every new `gencli` tool should
follow: it imports the tool's **pure functions** directly (no CLI, no
subprocess) and demonstrates their behavior, then shows the equivalent
CLI invocation.

`make convert-notebooks` turns this notebook into `docs/docs/notebooks/example.md`
for inclusion in the MkDocs site.


```python
%load_ext autoreload
%autoreload 2
```

## Quick check

Confirm the package is importable.


```python
from gencli import hello

hello()
```

## `slugify`

Pure function: given a string, returns a URL/filename-friendly slug.
No I/O, no randomness, no shared state — same input always returns the
same output.


```python
from gencli.commands.example import slugify

slugify("Hello, World! This is gencli.")
```

Equivalent CLI command:

```bash
gencli example slug "Hello, World! This is gencli."
```

## `word_count`

Pure function: given a string, returns a plain `dict` mapping word to
occurrence count. Case-insensitive, punctuation ignored.


```python
from gencli.commands.example import word_count

word_count("to be, or not to be")
```

Equivalent CLI command:

```bash
gencli example word-count "to be, or not to be"
```

## `reverse_words`

Pure function: reverses the order of whitespace-separated words.


```python
from gencli.commands.example import reverse_words

reverse_words("the quick brown fox")
```

Equivalent CLI command:

```bash
gencli example reverse "the quick brown fox"
```

## Composing pure functions

Because these are plain functions, they compose without touching the CLI
layer at all — useful for agents that want to call the logic directly
instead of shelling out.


```python
text = "The Quick Brown Fox Jumps Over The Lazy Dog"

slugify(reverse_words(text))
```

## Next steps

When you add a new tool under `gencli/commands/`:

1. Implement its logic as pure functions in that module.
2. Add thin Typer commands that call those functions (I/O only).
3. Register the sub-app in `gencli/main.py`.
4. Write unit tests for the pure functions and a couple of CLI wiring
   tests in `tests/`.
5. Copy this notebook to `docs/notebooks/<tool>.ipynb` and document the
   new functions the same way.
6. Run `make convert-notebooks` and list the new notebook in
   `docs/docs/index.md`.

See `AGENTS.md` for the full SDLC checklist.
