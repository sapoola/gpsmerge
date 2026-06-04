"""The interactive guided wizard -- the headline way to use gpsmerge.

Bare ``gpsmerge`` in a terminal lands here. The wizard walks a beginner through
one merge in plain language: what are you trying to do, which files, what did we
detect, which channel comes from where, what to call the device, where to write.
It collects answers into a :class:`~gpsmerge.config.Settings` and hands that to
:func:`gpsmerge.pipeline.run`, exactly as the ``run`` command does -- the two
paths share the same engine and the same exit codes.

The design is deliberately flat: :func:`run` calls one small step function per
question, top to bottom, and each step calls :mod:`questionary` and returns a
value. There is no wizard framework, no step registry, and no base class -- a
junior can read it as a story. Unhappy paths are first-class: a missing file is
re-prompted, a mode that contradicts the data offers the recommended switch, and
a cancelled prompt returns a usage exit code instead of a stack trace.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import questionary
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from gpsmerge.config import Settings
from gpsmerge.errors import EXIT_USAGE
from gpsmerge.merge import detect_mode
from gpsmerge.model import Activity
from gpsmerge.pipeline import RunResult
from gpsmerge.pipeline import run as run_pipeline
from gpsmerge.read import read
from gpsmerge.timeutil import local_range, utc_range, zone_label

# Extensions the wizard will accept as input files.
SUPPORTED_SUFFIXES = (".gpx", ".tcx")

# Minimum files a merge needs; fewer keeps the add-files loop going.
MIN_INPUTS = 2

# The sensor channels stacking can take from a chosen file, with the
# Settings field that names that file's source. Position is handled separately.
SENSOR_CHANNELS: tuple[tuple[str, str, str], ...] = (
    ("hr", "heart rate", "hr_from"),
    ("cad", "cadence", "cadence_from"),
    ("power", "power", "power_from"),
    ("temp", "temperature", "temp_from"),
)

_console = Console()


def run() -> int:
    """Run the guided merge wizard from start to finish.

    Returns
    -------
    int
        The process exit code: ``0`` on a successful (or declined) merge, the
        pipeline's exit code on a merge problem, and a usage code if the user
        cancelled a prompt.
    """
    _console.print(
        Panel.fit(
            "Let's merge your activity files. Press Ctrl-C any time to stop.",
            title="gpsmerge",
        )
    )

    try:
        mode = _ask_intent()
        paths = _ask_files()
        activities = [read(path) for path in paths]
        mode = _confirm_mode(activities, mode)

        channel_sources = (
            _ask_stack_sources(paths, activities) if mode == "stack" else {}
        )
        gap_policy, ignore_big_gaps = (
            _ask_append_options() if mode == "append" else ("pause", False)
        )

        device_from = _ask_device(paths, activities)
        output_format = _ask_output_format()
        timezone = _ask_timezone()
        sport = _ask_sport()
        clean_up = _ask_clean_up()
        output, assume_yes = _ask_output_path(paths, output_format)
    except _Cancelled:
        _console.print("[yellow]Cancelled. Nothing was written.[/yellow]")
        return EXIT_USAGE

    # Built via model_validate so pydantic checks the literal fields (mode,
    # format, gap policy) -- the same construction path the 'run' command uses.
    settings = Settings.model_validate(
        {
            "inputs": paths,
            "output": output,
            "output_format": output_format,
            "mode": mode,
            "position_from": channel_sources.get("position_from"),
            "hr_from": channel_sources.get("hr_from"),
            "cadence_from": channel_sources.get("cadence_from"),
            "power_from": channel_sources.get("power_from"),
            "temp_from": channel_sources.get("temp_from"),
            "gap_policy": gap_policy,
            "ignore_big_gaps_m": _BIG_GAP_LIMIT_M if ignore_big_gaps else None,
            "dedupe": clean_up,
            "recompute_distance": clean_up,
            "device_from": device_from,
            "sport": sport,
            "timezone": timezone,
            "assume_yes": assume_yes,
            "dry_run": True,  # preview first; the real write happens on confirm
        }
    )

    return _preview_then_write(settings)


# A jump larger than this many metres is dropped from the distance when the user
# asks to ignore big gaps. Wide enough to clear normal sampling, tight enough to
# catch a coffee stop or a GPS glitch.
_BIG_GAP_LIMIT_M = 250.0


class _Cancelled(Exception):
    """Raised internally when the user aborts a prompt (Ctrl-C)."""


def _ask(question: questionary.Question) -> object:
    """Ask one questionary question, turning a cancel into :class:`_Cancelled`.

    Parameters
    ----------
    question : questionary.Question
        The prepared prompt to display.

    Returns
    -------
    object
        The user's answer.

    Raises
    ------
    _Cancelled
        If the user aborted the prompt (questionary returns ``None``).
    """
    answer = question.ask()
    if answer is None:
        raise _Cancelled
    return answer


def _ask_intent() -> str:
    """Ask what the user is trying to do, returning a starting mode.

    Returns
    -------
    str
        ``"append"``, ``"stack"``, or ``"auto"`` (when unsure, let the data
        decide).
    """
    choice = _ask(
        questionary.select(
            "What do you want to do?",
            choices=[
                questionary.Choice(
                    "Append -- my ride was split across two recordings",
                    value="append",
                ),
                questionary.Choice(
                    "Stack -- two devices recorded the SAME ride", value="stack"
                ),
                questionary.Choice("Not sure -- look at my files", value="auto"),
            ],
            default="auto",
        )
    )
    return str(choice)


def _ask_files() -> list[Path]:
    """Prompt for input files one at a time until at least two are chosen.

    Each path is validated to exist and be a supported format; a bad path is
    explained and re-prompted. A blank entry finishes the list, but only once
    enough files have been added.

    Returns
    -------
    list of Path
        The chosen files, in the order given.
    """
    paths: list[Path] = []
    while True:
        entry = str(
            _ask(
                questionary.path(
                    _files_prompt(len(paths)),
                    only_directories=False,
                )
            )
        ).strip()

        if not entry:
            if len(paths) >= MIN_INPUTS:
                return paths
            _console.print(
                f"[yellow]Add at least {MIN_INPUTS} files to merge "
                f"(you have {len(paths)}).[/yellow]"
            )
            continue

        problem = _file_problem(Path(entry))
        if problem is not None:
            _console.print(f"[red]{problem}[/red]")
            continue

        paths.append(Path(entry))
        _show_file_list(paths)


def _files_prompt(count: int) -> str:
    """Return the add-files prompt text given how many files are chosen so far."""
    if count == 0:
        return "Add the first file:"
    return f"Add another file ({count} so far), or press Enter to finish:"


def _file_problem(path: Path) -> str | None:
    """Return why a path is not a usable input, or ``None`` if it is fine."""
    if not path.exists():
        return f"{path} does not exist."
    if not path.is_file():
        return f"{path} is not a file."
    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
        supported = " or ".join(SUPPORTED_SUFFIXES)
        return f"{path.name} is not a supported file ({supported})."
    return None


def _show_file_list(paths: list[Path]) -> None:
    """Print the running list of chosen files."""
    listing = "\n".join(f"  {i}. {p.name}" for i, p in enumerate(paths, start=1))
    _console.print(f"[green]Files so far:[/green]\n{listing}")


def _confirm_mode(activities: list[Activity], chosen: str) -> str:
    """Show what was detected and confirm or correct the mode.

    The detected mode (from time overlap) is shown alongside each file's span.
    When the user's choice contradicts the data, the recommended switch is
    offered as a choice rather than forcing it or crashing later.

    Parameters
    ----------
    activities : list of Activity
        The parsed inputs, used to measure spans and overlap.
    chosen : str
        The user's starting mode (``"append"``, ``"stack"`` or ``"auto"``).

    Returns
    -------
    str
        The mode to merge with: ``"append"`` or ``"stack"``.
    """
    detected = detect_mode(activities)
    _show_spans(activities, detected)

    if chosen == "auto":
        return _resolve_auto(detected)

    if detected != "ambiguous" and detected != chosen:
        return _offer_switch(chosen, detected)

    return chosen


def _resolve_auto(detected: str) -> str:
    """Pick a mode when the user was unsure, asking only if detection is unclear."""
    if detected in ("append", "stack"):
        _console.print(f"[green]Detected: {detected}.[/green]")
        return detected
    return _pick_mode("The files don't clearly stack or append. Which is it?")


def _offer_switch(chosen: str, detected: str) -> str:
    """Offer the recommended mode when the user's choice fights the data."""
    answer = _ask(
        questionary.select(
            f"You chose {chosen}, but the files look like a {detected}. "
            f"Which should I use?",
            choices=[
                questionary.Choice(
                    f"Use {detected} (recommended -- matches the data)",
                    value=detected,
                ),
                questionary.Choice(
                    f"Keep {chosen} (I know what I'm doing)", value=chosen
                ),
            ],
            default=detected,
        )
    )
    return str(answer)


def _pick_mode(message: str) -> str:
    """Ask the user to choose between append and stack directly."""
    answer = _ask(
        questionary.select(
            message,
            choices=[
                questionary.Choice("Append (back-to-back recordings)", value="append"),
                questionary.Choice("Stack (same ride, two devices)", value="stack"),
            ],
        )
    )
    return str(answer)


def _show_spans(activities: list[Activity], detected: str) -> None:
    """Print a table of each file's time span and the detected mode."""
    table = Table(title="What I see")
    table.add_column("File")
    table.add_column("From")
    table.add_column("To")
    for activity in activities:
        span = _span_text(activity)
        table.add_row(_file_label(activity), span[0], span[1])
    _console.print(table)
    _console.print(f"Looks like: [bold]{detected}[/bold]")


def _span_text(activity: Activity) -> tuple[str, str]:
    """Return an activity's first and last timestamps as short strings."""
    times = sorted(p.time for p in activity.points() if p.time is not None)
    if not times:
        return "(no time)", "(no time)"
    return _hhmmss(times[0]), _hhmmss(times[-1])


def _hhmmss(value: datetime) -> str:
    """Format a datetime as ``HH:MM:SS`` UTC for the spans table."""
    return value.strftime("%H:%M:%S")


def _ask_stack_sources(
    paths: list[Path], activities: list[Activity]
) -> dict[str, Path]:
    """Ask which file provides position and each shared sensor channel.

    Shows a coverage table (files by channels present), then asks which file
    supplies position. A channel only prompts when two or more files carry it;
    if just one file has it, that choice is stated and not asked.

    Parameters
    ----------
    paths : list of Path
        The input file paths, in order.
    activities : list of Activity
        The parsed inputs, aligned with ``paths`` by index.

    Returns
    -------
    dict
        Settings field names (``"position_from"``, ``"hr_from"`` ...) mapped to
        the chosen source path. Channels left to the default are omitted.
    """
    _show_coverage(paths, activities)
    sources: dict[str, Path] = {}

    position = _ask_position_source(paths, activities)
    if position is not None:
        sources["position_from"] = position

    for attr, label, field in SENSOR_CHANNELS:
        owners = [
            path
            for path, activity in zip(paths, activities)
            if _has_channel(activity, attr)
        ]
        if len(owners) >= 2:
            sources[field] = _ask_channel_source(label, owners)
    return sources


def _ask_position_source(paths: list[Path], activities: list[Activity]) -> Path | None:
    """Ask which file provides position, or state it when only one has GPS."""
    owners = [
        path for path, activity in zip(paths, activities) if _has_position(activity)
    ]
    if len(owners) <= 1:
        chosen = owners[0] if owners else None
        if chosen is not None:
            _console.print(
                f"[green]Position comes from {chosen.name} "
                f"(the only file with GPS).[/green]"
            )
        return chosen
    return _ask_channel_source("position", owners)


def _ask_channel_source(label: str, owners: list[Path]) -> Path:
    """Ask which of the files that carry a channel should provide it."""
    answer = _ask(
        questionary.select(
            f"Which file provides {label}?",
            choices=[questionary.Choice(p.name, value=str(p)) for p in owners],
            default=str(owners[0]),
        )
    )
    return Path(str(answer))


def _show_coverage(paths: list[Path], activities: list[Activity]) -> None:
    """Print a table of which channels each file carries."""
    table = Table(title="Channel coverage")
    table.add_column("File")
    table.add_column("Position")
    for _, label, _ in SENSOR_CHANNELS:
        table.add_column(label.title())
    for path, activity in zip(paths, activities):
        cells = [_tick(_has_position(activity))]
        cells += [_tick(_has_channel(activity, attr)) for attr, _, _ in SENSOR_CHANNELS]
        table.add_row(path.name, *cells)
    _console.print(table)


def _tick(present: bool) -> str:
    """Return a green check or a dim dash for a coverage cell."""
    return "[green]yes[/green]" if present else "[dim]-[/dim]"


def _has_position(activity: Activity) -> bool:
    """Return whether any point in the activity carries a position."""
    return any(p.lat is not None and p.lon is not None for p in activity.points())


def _has_channel(activity: Activity, attr: str) -> bool:
    """Return whether any point in the activity carries the given channel."""
    return any(getattr(p, attr) is not None for p in activity.points())


def _ask_append_options() -> tuple[str, bool]:
    """Ask the gap policy and whether to ignore big gaps when appending.

    Returns
    -------
    tuple
        ``(gap_policy, ignore_big_gaps)`` where ``gap_policy`` is ``"pause"`` or
        ``"shift"`` and ``ignore_big_gaps`` is a bool.
    """
    policy = _ask(
        questionary.select(
            "There may be a gap between the recordings. How should I treat it?",
            choices=[
                questionary.Choice(
                    "Pause -- keep the real times (gap shows as stopped)",
                    value="pause",
                ),
                questionary.Choice(
                    "Shift -- move the later ride to start right after the first",
                    value="shift",
                ),
            ],
            default="pause",
        )
    )
    ignore = bool(
        _ask(
            questionary.confirm(
                "Keep big position jumps out of the distance total?",
                default=False,
            )
        )
    )
    return str(policy), ignore


def _ask_device(paths: list[Path], activities: list[Activity]) -> Path | None:
    """Ask which source file's recording device the merged file is attributed to.

    Returns
    -------
    Path or None
        The chosen file's path, or ``None`` to keep the pipeline default.
    """
    choices = [
        questionary.Choice(_device_label(activity, path), value=str(path))
        for path, activity in zip(paths, activities)
    ]
    answer = _ask(
        questionary.select(
            "Which device should the merged file be recorded under?",
            choices=choices,
            default=str(paths[0]),
        )
    )
    return Path(str(answer))


def _device_label(activity: Activity, path: Path) -> str:
    """Return a readable device choice label: device name (falling back to file)."""
    name = activity.device.name
    if name:
        return f"{name}  ({path.name})"
    return f"{path.name}  (unnamed device)"


def _ask_output_format() -> str:
    """Ask which format to write, defaulting to GPX."""
    answer = _ask(
        questionary.select(
            "Output format?",
            choices=[
                questionary.Choice("GPX (works everywhere)", value="gpx"),
                questionary.Choice("TCX (no temperature channel)", value="tcx"),
            ],
            default="gpx",
        )
    )
    return str(answer)


def _ask_timezone() -> str | None:
    """Ask which timezone to show the preview's times in, defaulting to UTC only.

    Display-only: it changes the preview, never the written file's timestamps. A
    blank answer keeps the preview in UTC.

    Returns
    -------
    str or None
        The raw timezone spec, or ``None`` when left blank.
    """
    answer = str(
        _ask(
            questionary.text(
                "Timezone these were recorded in? "
                "(e.g. Europe/Amsterdam or +02:00, Enter to skip)",
                default="",
            )
        )
    ).strip()
    return answer or None


def _ask_sport() -> str | None:
    """Ask whether to override the sport, defaulting to keeping the source's."""
    answer = str(
        _ask(
            questionary.text(
                "Sport for the merged file? (leave blank to keep the source's)",
                default="",
            )
        )
    ).strip()
    return answer or None


def _ask_clean_up() -> bool:
    """Ask whether to apply the standard clean-up (dedupe and recompute distance)."""
    return bool(
        _ask(
            questionary.confirm(
                "Apply standard clean-up (drop seam duplicates, recompute "
                "distance)?",
                default=True,
            )
        )
    )


def _ask_output_path(paths: list[Path], output_format: str) -> tuple[Path, bool]:
    """Ask where to write, handling an existing file gracefully.

    Defaults to ``merged.<format>`` in the inputs' directory. If the chosen path
    already exists, the user picks overwrite, a new name, or cancel.

    Parameters
    ----------
    paths : list of Path
        The inputs, used to default the output directory.
    output_format : str
        The chosen format, used for the default file extension.

    Returns
    -------
    tuple
        ``(output_path, assume_yes)`` -- ``assume_yes`` is ``True`` when the user
        confirmed an overwrite, so the pipeline does not block on it.

    Raises
    ------
    _Cancelled
        If the user chooses to cancel at the existing-file prompt.
    """
    default = paths[0].parent / f"merged.{output_format}"
    while True:
        entry = str(
            _ask(
                questionary.path(
                    "Where should I write the merged file?",
                    default=str(default),
                    only_directories=False,
                )
            )
        ).strip()
        output = Path(entry) if entry else default

        if not output.exists():
            return output, False

        decision = _ask(
            questionary.select(
                f"{output.name} already exists. What now?",
                choices=[
                    questionary.Choice("Overwrite it", value="overwrite"),
                    questionary.Choice("Pick a different name", value="rename"),
                    questionary.Choice("Cancel", value="cancel"),
                ],
                default="rename",
            )
        )
        if decision == "overwrite":
            return output, True
        if decision == "cancel":
            raise _Cancelled
        # "rename": loop and ask for a new path.


def _preview_then_write(settings: Settings) -> int:
    """Show the preview, confirm, and run the real write.

    Parameters
    ----------
    settings : Settings
        The collected settings, with ``dry_run`` set so the first run only
        previews.

    Returns
    -------
    int
        ``0`` on success or a declined write; the pipeline's exit code otherwise.
    """
    preview_result = run_pipeline(settings)
    if preview_result.issues:
        _report_issues(preview_result)
        return preview_result.issues[0].code

    _show_preview(preview_result)

    write_it = bool(_safe_confirm("Write it?", default=True))
    if not write_it:
        _console.print("[yellow]Okay, nothing written.[/yellow]")
        return 0

    final = run_pipeline(settings.model_copy(update={"dry_run": False}))
    if final.issues:
        _report_issues(final)
        return final.issues[0].code

    _console.print(f"[green]Wrote {final.output}[/green]")
    return 0


def _safe_confirm(message: str, *, default: bool) -> bool:
    """Confirm, treating a cancel as 'no' so the final step never crashes."""
    answer = questionary.confirm(message, default=default).ask()
    return bool(answer) if answer is not None else False


def _show_preview(result: RunResult) -> None:
    """Render the rich preview panel for a successful dry run."""
    preview = result.preview
    assert preview is not None  # success always carries a preview

    table = Table.grid(padding=(0, 2))
    table.add_column(justify="right", style="bold")
    table.add_column()
    table.add_row("points", str(preview.point_count))
    table.add_row(
        "moving",
        f"{_duration(preview.moving_seconds)}  "
        f"(elapsed {_duration(preview.elapsed_seconds)})",
    )
    table.add_row("distance", f"{preview.distance_m / 1000:.2f} km")
    table.add_row("channels", ", ".join(preview.channels) or "none")
    table.add_row("device", preview.device_name or "unknown")
    if preview.start_time is not None and preview.end_time is not None:
        table.add_row("time", utc_range(preview.start_time, preview.end_time))
    if preview.local_start is not None and preview.local_end is not None:
        zone = zone_label(preview.timezone_label, preview.local_start)
        table.add_row(
            "local",
            f"{local_range(preview.local_start, preview.local_end)}  ({zone})",
        )
    _console.print(Panel(table, title="Preview", expand=False))

    for warning in preview.warnings:
        _console.print(f"[yellow]! {warning}[/yellow]")


def _report_issues(result: RunResult) -> None:
    """Print the blocking problems from a failed run."""
    for issue in result.issues:
        _console.print(f"[red]error: {issue.message}[/red]")


def _file_label(activity: Activity) -> str:
    """Return a short label for an activity in tables: its file name."""
    if activity.source_path is not None:
        return Path(activity.source_path).name
    return activity.name or "an input file"


def _duration(seconds: float) -> str:
    """Format a number of seconds as ``1h 02m 03s``."""
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}h {minutes:02d}m {secs:02d}s"
