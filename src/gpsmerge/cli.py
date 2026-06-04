"""Command-line entry point for gpsmerge.

Bare ``gpsmerge`` will launch a guided wizard in a later milestone; for now the
useful command is ``gpsmerge run``, which merges files non-interactively. Every
flag maps onto a single :class:`~gpsmerge.config.Settings`, the pipeline does the
work, and this module only turns the result into text (or JSON) and an exit code.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer
from pydantic import ValidationError

from gpsmerge import __version__
from gpsmerge.config import Settings
from gpsmerge.errors import EXIT_USAGE
from gpsmerge.pipeline import RunResult
from gpsmerge.pipeline import run as run_pipeline

app = typer.Typer(
    name="gpsmerge",
    help="Merge GPX, TCX, and FIT activity files entirely on your machine.",
    add_completion=False,
)


def _print_version(value: bool) -> None:
    """Print the version and exit when ``--version`` is given."""
    if value:
        typer.echo(f"gpsmerge {__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def main_callback(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        help="Show the version and exit.",
        callback=_print_version,
        is_eager=True,
    ),
) -> None:
    """Combine GPS activity files offline."""
    if ctx.invoked_subcommand is None:
        typer.echo(
            "Nothing to do. Try 'gpsmerge run --help' to merge files; "
            "the guided wizard arrives in a later version."
        )


@app.command()
def run(
    inputs: list[Path] = typer.Argument(
        ..., help="The GPS files to merge, in order (two or more)."
    ),
    output: Path = typer.Option(
        ..., "--out", "-o", help="Where to write the merged file."
    ),
    mode: str = typer.Option(
        "append", "--mode", help="Merge mode. Only 'append' is available so far."
    ),
    gap_policy: str = typer.Option(
        "pause",
        "--gap-policy",
        help="How to treat the gap between recordings: 'pause' or 'shift'.",
    ),
    ignore_gaps_over_meters: float | None = typer.Option(
        None,
        "--ignore-gaps-over-meters",
        help="Do not count a jump larger than this many metres towards distance.",
    ),
    dedupe: bool = typer.Option(
        True, "--dedupe/--no-dedupe", help="Drop a duplicated point at the seam."
    ),
    recompute_distance: bool = typer.Option(
        True,
        "--recompute-distance/--no-recompute-distance",
        help="Recompute distance from positions after merging.",
    ),
    device_from: Path | None = typer.Option(
        None,
        "--device-from",
        help="Which input file's device the merged file is recorded under "
        "(default: the first input).",
    ),
    sport: str | None = typer.Option(
        None, "--sport", help="Override the sport of the merged activity."
    ),
    assume_yes: bool = typer.Option(
        False, "--yes", "-y", help="Overwrite the output without asking."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show the preview but do not write the file."
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Print a machine-readable summary instead of text."
    ),
) -> None:
    """Merge GPS files into one. Currently appends sequential recordings."""
    if mode != "append":
        typer.secho(
            f"only 'append' mode is available so far (got {mode!r})",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(EXIT_USAGE)

    try:
        settings = Settings.model_validate(
            {
                "inputs": inputs,
                "output": output,
                "gap_policy": gap_policy,
                "ignore_big_gaps_m": ignore_gaps_over_meters,
                "dedupe": dedupe,
                "recompute_distance": recompute_distance,
                "device_from": device_from,
                "sport": sport,
                "assume_yes": assume_yes,
                "dry_run": dry_run,
                "json_output": json_output,
            }
        )
    except ValidationError as exc:
        typer.secho(_first_error(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(EXIT_USAGE) from exc

    result = run_pipeline(settings)
    _report(result, json_output)
    raise typer.Exit(result.issues[0].code if result.issues else 0)


def _report(result: RunResult, json_output: bool) -> None:
    """Print the outcome of a run as either text or JSON."""
    if result.issues:
        issue = result.issues[0]
        if json_output:
            typer.echo(
                json.dumps({"error": issue.message, "code": issue.code}), err=True
            )
        else:
            for problem in result.issues:
                typer.secho(f"error: {problem.message}", fg=typer.colors.RED, err=True)
        return

    if json_output:
        typer.echo(json.dumps(_summary(result)))
    else:
        _print_preview(result)


def _summary(result: RunResult) -> dict[str, object]:
    """Build the machine-readable summary for a successful run."""
    preview = result.preview
    assert preview is not None  # success always carries a preview
    return {
        "output": str(result.output) if result.output is not None else None,
        "written": result.written,
        "points": preview.point_count,
        "moving_seconds": preview.moving_seconds,
        "elapsed_seconds": preview.elapsed_seconds,
        "distance_m": preview.distance_m,
        "channels": preview.channels,
        "device": preview.device_name,
        "warnings": preview.warnings,
    }


def _print_preview(result: RunResult) -> None:
    """Print the human-readable preview of a successful run."""
    preview = result.preview
    assert preview is not None
    verb = "Wrote" if result.written else "Would write"
    typer.echo(f"{verb} {result.output}")
    typer.echo(f"  points:   {preview.point_count}")
    typer.echo(
        f"  moving:   {_format_duration(preview.moving_seconds)}  "
        f"(elapsed {_format_duration(preview.elapsed_seconds)})"
    )
    typer.echo(f"  distance: {preview.distance_m / 1000:.2f} km")
    typer.echo(f"  channels: {', '.join(preview.channels) or 'none'}")
    typer.echo(f"  device:   {preview.device_name or 'unknown'}")
    for warning in preview.warnings:
        typer.secho(f"  ! {warning}", fg=typer.colors.YELLOW)


def _format_duration(seconds: float) -> str:
    """Format a number of seconds as ``1h 02m 03s``."""
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}h {minutes:02d}m {secs:02d}s"


def _first_error(exc: ValidationError) -> str:
    """Turn the first validation error into a short, readable message."""
    errors = exc.errors()
    if not errors:
        return str(exc)
    location = ".".join(str(part) for part in errors[0]["loc"])
    return f"invalid {location}: {errors[0]['msg']}"


def main() -> None:
    """Console-script entry point referenced by ``[project.scripts]``."""
    app()
