"""
Example command module.

This module is the reference template for adding new tools to the CLI.
It demonstrates the pattern every command module should follow:

1. Pure functions hold all the logic. They take plain values in, return
   plain values out, and have no side effects (no I/O, no printing, no
   mutation of arguments or module state). These are the functions an
   agent or another module would import and call directly.
2. Typer commands are thin adapters. They parse CLI arguments/options,
   call the pure functions, and handle the only side effects allowed at
   this layer: reading input and printing/writing output.

See AGENTS.md for the full SDLC guidelines (tests + notebook docs are
required for every new pure function exposed here).
"""

import re
from collections import Counter
from typing import Dict

import typer
from rich.console import Console

app = typer.Typer(
    no_args_is_help=True,
    help="Example text utilities. Reference implementation for new tools.",
)

console = Console()

_WORD_RE = re.compile(r"[A-Za-z0-9']+")


# --------------------------------------------------------------------------
# Pure functions (no I/O, no side effects, deterministic output for a given
# input). These are what should be imported and unit-tested directly, and
# what should be demonstrated in a docs notebook.
# --------------------------------------------------------------------------


def slugify(text: str) -> str:
    """
    Convert arbitrary text into a URL/filename-friendly slug.

    Lowercases the text, strips characters that are not alphanumeric,
    and joins words with hyphens.
    """
    words = _WORD_RE.findall(text.lower())
    return "-".join(words)


def word_count(text: str) -> Dict[str, int]:
    """
    Count occurrences of each word in ``text``.

    Words are matched case-insensitively and punctuation is ignored.
    Returns a plain dict mapping word -> count.
    """
    words = _WORD_RE.findall(text.lower())
    return dict(Counter(words))


def reverse_words(text: str) -> str:
    """Reverse the order of whitespace-separated words in ``text``."""
    return " ".join(reversed(text.split()))


# --------------------------------------------------------------------------
# Typer commands (thin I/O adapters over the pure functions above).
# --------------------------------------------------------------------------


@app.command()
def slug(
    text: str = typer.Argument(..., help="Text to convert into a slug"),
) -> None:
    """
    Print a URL/filename-friendly slug for TEXT.

    Example:
        gencli example slug "Hello, World!"
    """
    console.print(slugify(text))


@app.command(name="word-count")
def word_count_command(
    text: str = typer.Argument(..., help="Text to count words in"),
) -> None:
    """
    Print word occurrence counts for TEXT, most frequent first.

    Example:
        gencli example word-count "to be or not to be"
    """
    counts = word_count(text)
    for word, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        console.print(f"{word}: {count}")


@app.command()
def reverse(
    text: str = typer.Argument(
        ..., help="Text whose word order should be reversed"
    ),
) -> None:
    """
    Print TEXT with its word order reversed.

    Example:
        gencli example reverse "the quick brown fox"
    """
    console.print(reverse_words(text))


if __name__ == "__main__":
    app()
