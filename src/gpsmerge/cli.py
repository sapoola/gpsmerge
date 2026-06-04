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
from datetime import datetime
from pathlib import Path

import typer
from pydantic import ValidationError

from gpsmerge import __version__, profiles, wizard
from gpsmerge.config import Settings
from gpsmerge.errors import EXIT_USAGE
from gpsmerge.pipeline import RunResult
from gpsmerge.pipeline import run as run_pipeline
from gpsmerge.timeutil import local_range, utc_range, zone_label

app = typer.Typer(
    name="gpsmerge",
    help="Merge GPX, TCX, and FIT activity files entirely on your machine.",
    add_completion=False,
)

# Each saveable profile key paired with the 'run' parameter that carries it.
# Resolving a profile means, for each pair, choosing the command-line value when
# the user typed the flag and the profile's value otherwise (flag > profile).
_PROFILE_KEY_TO_PARAM: tuple[tuple[str, str], ...] = (
    ("mode", "mode"),
    ("output_format", "output_format"),
    ("max_gap_s", "max_gap_seconds"),
    ("gap_policy", "gap_policy"),
    ("ignore_big_gaps_m", "ignore_gaps_over_meters"),
    ("dedupe", "dedupe"),
    ("recompute_distance", "recompute_distance"),
    ("sport", "sport"),
    ("timezone", "timezone"),
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
    ctx: typer.Context,
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
    timezone: str | None = typer.Option(
        None,
        "--timezone",
        help="Show the preview's times in this zone (display only; the file "
        "stays UTC). An IANA name like 'Europe/Amsterdam' (auto-handles summer "
        "time) or a fixed offset like '+02:00'.",
    ),
    shift: str | None = typer.Option(
        None,
        "--shift",
        help="Add a signed duration to every timestamp to fix a wrong device "
        "clock, e.g. '+1h', '-30m', '90s', or '+1:30:00'.",
    ),
    trim_start: str | None = typer.Option(
        None,
        "--trim-start",
        help="Drop points before this ISO time, e.g. '2026-06-04T08:30:00Z'.",
    ),
    trim_end: str | None = typer.Option(
        None,
        "--trim-end",
        help="Drop points after this ISO time, e.g. '2026-06-04T09:30:00Z'.",
    ),
    smooth_elevation: int | None = typer.Option(
        None,
        "--smooth-elevation",
        help="Smooth elevation with a centred moving average of this window "
        "(an odd integer >= 3).",
    ),
    insert_stopped_time: bool = typer.Option(
        False,
        "--insert-stopped-time/--no-insert-stopped-time",
        help="Split the track at long non-moving gaps so they count as pauses, "
        "not moving time.",
    ),
    stopped_time_threshold: float = typer.Option(
        30.0,
        "--stopped-time-threshold",
        help="With --insert-stopped-time, a gap longer than this many seconds "
        "becomes a pause.",
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
    profile: str | None = typer.Option(
        None,
        "--profile",
        help="Load saved settings as a base (flags you also pass win over it).",
    ),
    save_profile: str | None = typer.Option(
        None,
        "--save-profile",
        help="Save the general settings of this run under a name for reuse.",
    ),
) -> None:
    """Merge GPS files into one: append sequential rides, or stack same-time ones."""
    flag_values = {
        "mode": mode,
        "output_format": output_format,
        "max_gap_seconds": max_gap_seconds,
        "gap_policy": gap_policy,
        "ignore_gaps_over_meters": ignore_gaps_over_meters,
        "dedupe": dedupe,
        "recompute_distance": recompute_distance,
        "sport": sport,
        "timezone": timezone,
    }
    saved: dict[str, object] = {}
    if profile is not None:
        try:
            saved = profiles.load_profile(profile)
        except ValueError as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(EXIT_USAGE) from exc
    # Validate the save name up front so a typo fails fast, not after a merge.
    if save_profile is not None:
        try:
            profiles.validate_name(save_profile)
        except ValueError as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(EXIT_USAGE) from exc

    resolved = _resolve_profile(ctx, saved, flag_values)

    try:
        settings = Settings.model_validate(
            {
                "inputs": inputs,
                "output": output,
                "output_format": resolved["output_format"],
                "mode": resolved["mode"],
                "max_gap_s": resolved["max_gap_seconds"],
                "position_from": position_from,
                "hr_from": hr_from,
                "cadence_from": cadence_from,
                "power_from": power_from,
                "temp_from": temp_from,
                "gap_policy": resolved["gap_policy"],
                "ignore_big_gaps_m": resolved["ignore_gaps_over_meters"],
                "dedupe": resolved["dedupe"],
                "recompute_distance": resolved["recompute_distance"],
                "device_from": device_from,
                "sport": resolved["sport"],
                "timezone": resolved["timezone"],
                "shift": shift,
                "trim_start": trim_start,
                "trim_end": trim_end,
                "smooth_elevation": smooth_elevation,
                "insert_stopped_time": insert_stopped_time,
                "stopped_time_threshold_s": stopped_time_threshold,
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
    # Save only a run that worked (a dry run still counts): a profile built from a
    # merge that erred out would pin settings the user never got to see succeed.
    if save_profile is not None and not result.issues:
        profiles.save_profile(save_profile, _chosen_profile(ctx, saved, settings))
        typer.echo(f"saved profile '{save_profile}'")
    raise typer.Exit(result.issues[0].code if result.issues else 0)


def _resolve_profile(
    ctx: typer.Context, saved: dict[str, object], flag_values: dict[str, object]
) -> dict[str, object]:
    """Merge a loaded profile under the run flags, with flags taking precedence.

    For each saveable key: an explicitly typed flag wins, else the profile's
    value, else the flag's default. A flag is "explicit" when Click reports its
    source as the command line.

    Parameters
    ----------
    ctx : typer.Context
        The command's context, used to ask Click where each value came from.
    saved : dict
        The loaded profile's values by saveable settings key (``{}`` for none).
    flag_values : dict
        The current value of each saveable run parameter, by parameter name.

    Returns
    -------
    dict
        The resolved value for every saveable run parameter, by parameter name.
    """
    resolved = dict(flag_values)
    for key, param in _PROFILE_KEY_TO_PARAM:
        if not _flag_was_set(ctx, param) and key in saved:
            resolved[param] = saved[key]
    return resolved


def _chosen_profile(
    ctx: typer.Context, saved: dict[str, object], settings: Settings
) -> dict[str, object]:
    """Return the settings worth saving: the ones the user actually chose.

    Only keys whose flag was typed on the command line, or that came from a
    loaded ``--profile``, are saved -- untouched defaults are left out, so a
    profile captures intent (e.g. "TCX in Amsterdam time"), not the whole config.

    Parameters
    ----------
    ctx : typer.Context
        The command's context, used to ask Click which flags were typed.
    saved : dict
        The profile that was loaded with ``--profile`` (``{}`` for none).
    settings : Settings
        The resolved settings of this run.

    Returns
    -------
    dict
        The saveable keys the user chose, mapped to their values.
    """
    chosen: dict[str, object] = {}
    for key, param in _PROFILE_KEY_TO_PARAM:
        if _flag_was_set(ctx, param) or key in saved:
            value = getattr(settings, key)
            if value is not None:
                chosen[key] = value
    return chosen


def _flag_was_set(ctx: typer.Context, param: str) -> bool:
    """Return whether a run flag was given on the command line (not the default).

    Compares the parameter source by name rather than by enum identity: Typer
    vendors its own Click, so the source enum the context returns is not the same
    object as a top-level ``click`` import would give.
    """
    source = ctx.get_parameter_source(param)
    return source is not None and source.name == "COMMANDLINE"


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
    typer.echo("  read:  gpx, tcx, fit")
    typer.echo("  write: gpx, tcx")
    typer.echo("")
    typer.echo("Channels carried by each format:")
    typer.echo("  gpx: position, elevation, hr, cadence, power, temperature, speed")
    typer.echo("  tcx: position, elevation, hr, cadence, power, speed (no temperature)")
    typer.echo("  fit: position, elevation, hr, cadence, power, temperature, speed")
    typer.echo("")
    typer.echo("Notes: FIT is read-only for now; writing FIT is a later milestone.")
    typer.echo("       TCX has no temperature channel; it is dropped on write.")


profile_app = typer.Typer(help="Save and reuse general merge settings by name.")
app.add_typer(profile_app, name="profile")


@profile_app.command("list")
def profile_list() -> None:
    """List the saved profiles."""
    names = profiles.list_profiles()
    if not names:
        typer.echo("no profiles yet")
        return
    for name in names:
        typer.echo(name)


@profile_app.command("show")
def profile_show(name: str = typer.Argument(..., help="The profile to show.")) -> None:
    """Print a profile's saved settings as ``key=value`` lines."""
    try:
        data = profiles.load_profile(name)
    except ValueError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(EXIT_USAGE) from exc
    for key, value in data.items():
        typer.echo(f"{key}={value}")


@profile_app.command("rm")
def profile_rm(name: str = typer.Argument(..., help="The profile to delete.")) -> None:
    """Delete a saved profile."""
    try:
        profiles.delete_profile(name)
    except ValueError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(EXIT_USAGE) from exc
    typer.echo(f"deleted profile '{name}'")


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
    summary: dict[str, object] = {
        "output": str(result.output) if result.output is not None else None,
        "written": result.written,
        "points": preview.point_count,
        "moving_seconds": preview.moving_seconds,
        "elapsed_seconds": preview.elapsed_seconds,
        "distance_m": preview.distance_m,
        "channels": preview.channels,
        "device": preview.device_name,
        "warnings": preview.warnings,
        "start_utc": _iso_or_none(preview.start_time),
        "end_utc": _iso_or_none(preview.end_time),
    }
    if preview.local_start is not None:
        summary["local_start"] = _iso_or_none(preview.local_start)
        summary["local_end"] = _iso_or_none(preview.local_end)
        summary["timezone"] = preview.timezone_label
    return summary


def _iso_or_none(value: datetime | None) -> str | None:
    """Return a datetime's ISO-8601 string, or ``None`` if there is none."""
    return value.isoformat() if value is not None else None


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
    if preview.start_time is not None and preview.end_time is not None:
        typer.echo(f"  time:     {utc_range(preview.start_time, preview.end_time)}")
    if preview.local_start is not None and preview.local_end is not None:
        typer.echo(
            f"  local:    "
            f"{local_range(preview.local_start, preview.local_end)}  "
            f"({zone_label(preview.timezone_label, preview.local_start)})"
        )
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
