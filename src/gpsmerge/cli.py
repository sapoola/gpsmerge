"""Command-line entry point for gpsmerge.

Bare ``gpsmerge`` in a terminal launches the guided wizard (see
:mod:`gpsmerge.wizard`); piped or in CI it prints a hint instead, so
non-interactive use never hangs. The ``run`` command merges files
non-interactively: every flag maps onto a single
:class:`~gpsmerge.config.Settings`, the pipeline does the work, and this module
only turns the result into text (or JSON) and an exit code. ``examples`` and
``formats`` print quick reference for people new to the tool.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer
from pydantic import ValidationError

from gpsmerge import __version__, wizard
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
    if ctx.invoked_subcommand is not None:
        return
    # In a terminal, bare 'gpsmerge' is the headline experience: the wizard.
    # Piped or in CI (no TTY), never block on prompts -- just point the way.
    if sys.stdin.isatty():
        raise typer.Exit(wizard.run())
    typer.echo(
        "Nothing to do. Run 'gpsmerge' in a terminal for the guided wizard, "
        "or 'gpsmerge run --help' to merge files directly."
    )


@app.command()
def run(
    inputs: list[Path] = typer.Argument(
        ..., help="The GPS files to merge, in order (two or more)."
    ),
    output: Path = typer.Option(
        ..., "--out", "-o", help="Where to write the merged file."
    ),
    output_format: str = typer.Option(
        "gpx", "--format", help="Output format: 'gpx' or 'tcx'."
    ),
    mode: str = typer.Option(
        "auto",
        "--mode",
        help="How to combine the files: 'auto', 'append', or 'stack'.",
    ),
    max_gap_seconds: float = typer.Option(
        10.0,
        "--max-gap-seconds",
        help="When stacking, do not interpolate a channel across a source gap "
        "longer than this many seconds.",
    ),
    position_from: Path | None = typer.Option(
        None,
        "--position-from",
        help="When stacking, which input provides position (default: best GPS).",
    ),
    hr_from: Path | None = typer.Option(
        None, "--hr-from", help="When stacking, which input provides heart rate."
    ),
    cadence_from: Path | None = typer.Option(
        None, "--cadence-from", help="When stacking, which input provides cadence."
    ),
    power_from: Path | None = typer.Option(
        None, "--power-from", help="When stacking, which input provides power."
    ),
    temp_from: Path | None = typer.Option(
        None, "--temp-from", help="When stacking, which input provides temperature."
    ),
    gap_policy: str = typer.Option(
        "pause",
        "--gap-policy",
        help="When appending, how to treat the gap between recordings: "
        "'pause' or 'shift'.",
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
    """Merge GPS files into one: append sequential rides, or stack same-time ones."""
    try:
        settings = Settings.model_validate(
            {
                "inputs": inputs,
                "output": output,
                "output_format": output_format,
                "mode": mode,
                "max_gap_s": max_gap_seconds,
                "position_from": position_from,
                "hr_from": hr_from,
                "cadence_from": cadence_from,
                "power_from": power_from,
                "temp_from": temp_from,
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


@app.command()
def examples() -> None:
    """Print copy-pasteable example commands."""
    typer.echo("Examples (replace the paths with your own files):")
    typer.echo("")
    typer.echo("  # Append: a ride split across two recordings, into one GPX")
    typer.echo("  gpsmerge run morning.gpx evening.gpx --out ride.gpx")
    typer.echo("")
    typer.echo("  # Stack: GPS from a watch, power and HR from a trainer")
    typer.echo(
        "  gpsmerge run watch.gpx trainer.tcx --mode stack \\\n"
        "    --position-from watch.gpx --power-from trainer.tcx --out ride.gpx"
    )
    typer.echo("")
    typer.echo("  # Auto: let gpsmerge decide whether to append or stack")
    typer.echo("  gpsmerge run a.gpx b.gpx --out merged.gpx")
    typer.echo("")
    typer.echo("  # Preview only, no file written")
    typer.echo("  gpsmerge run a.gpx b.gpx --out merged.gpx --dry-run")


@app.command()
def formats() -> None:
    """Print the supported formats and which channels each carries."""
    typer.echo("Supported formats:")
    typer.echo("  read:  gpx, tcx")
    typer.echo("  write: gpx, tcx")
    typer.echo("")
    typer.echo("Channels carried by each format:")
    typer.echo("  gpx: position, elevation, hr, cadence, power, temperature, speed")
    typer.echo("  tcx: position, elevation, hr, cadence, power, speed (no temperature)")
    typer.echo("")
    typer.echo("Note: TCX has no temperature channel; it is dropped on write.")


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
