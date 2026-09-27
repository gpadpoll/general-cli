"""
Entrypoint for CLI.

This is the main entry point for the gencli CLI application. It wires up
each command module (a Typer sub-app) under a top-level name. Add new
tools by creating a module in `gencli/commands/`, implementing the tool's
logic as pure functions with thin Typer command wrappers (see
`gencli/commands/example.py`), and registering it here.
"""

import typer

from gencli.commands import config, example

app = typer.Typer(
    no_args_is_help=True,
    help="General-purpose CLI: a library of functions exposed as commands.",
    rich_markup_mode="rich",
)
app.add_typer(config.app, name="config", help="manage configuration settings")
app.add_typer(example.app, name="example", help="reference text utilities")


def gencli() -> None:
    app()


if __name__ == "__main__":
    app()
