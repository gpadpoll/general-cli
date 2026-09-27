"""
Object initialization for CLI
"""

from rich.console import Console

console = Console()


def hello() -> None:
    """Print a friendly greeting confirming the package is importable."""
    console.print("Hello from gencli!")
