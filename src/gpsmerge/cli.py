"""Command-line entry point for gpsmerge.

This is intentionally tiny right now. Later milestones add the merge commands and
the interactive wizard; for the scaffold it only knows how to report its version.
"""

from __future__ import annotations

import typer

from gpsmerge import __version__

app = typer.Typer(
    name="gpsmerge",
    help="Merge GPX, TCX, and FIT activity files entirely on your machine.",
    add_completion=False,
)


def _print_version(value: bool) -> None:
    """Print the version and exit when ``--version`` is given.

    Parameters
    ----------
    value : bool
        True when the user passed ``--version``; otherwise do nothing.
    """
    if value:
        typer.echo(f"gpsmerge {__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def main_callback(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        help="Show the version and exit.",
        callback=_print_version,
        is_eager=True,
    ),
) -> None:
    """Combine GPS activity files offline.

    Running ``gpsmerge`` with no command will launch the guided wizard in a later
    milestone. For now it is a no-op unless ``--version`` is given.
    """


def main() -> None:
    """Console-script entry point referenced by ``[project.scripts]``."""
    app()
