"""Check that a set of inputs can actually be merged, before doing any work.

:func:`validate` runs once, after the files are read and before the merge, and
returns a list of :class:`~gpsmerge.errors.Issue`. An empty list means "go
ahead". The same list drives both the interactive recovery prompts (later) and
the non-interactive exit codes, so a problem is explained the same way however
the tool is run.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from gpsmerge.config import Settings
from gpsmerge.errors import (
    EXIT_MODE_CONFLICT,
    EXIT_PARSE,
    EXIT_USAGE,
    EXIT_WRITE,
    Issue,
)
from gpsmerge.model import Activity
from gpsmerge.util.timeutil import parse_duration, parse_time, parse_timezone

MIN_INPUTS_TO_MERGE = 2

# A smoothing window must be odd (centred) and at least this wide (so it averages).
MIN_SMOOTH_WINDOW = 3


def validate(activities: list[Activity], settings: Settings) -> list[Issue]:
    """Return every blocking problem with the requested merge, in order found.

    Parameters
    ----------
    activities : list of Activity
        The parsed input activities, in the order given.
    settings : Settings
        The merge options, including the mode and the output path.

    Returns
    -------
    list of Issue
        Empty if the merge can proceed; otherwise one entry per problem.
    """
    if len(activities) < MIN_INPUTS_TO_MERGE:
        return [
            Issue(
                EXIT_USAGE,
                f"merging needs at least {MIN_INPUTS_TO_MERGE} files "
                f"(got {len(activities)})",
            )
        ]

    issues: list[Issue] = []
    issues.extend(_check_timestamps(activities))
    if not issues:  # overlap can only be judged once every file has times
        issues.extend(_check_mode_vs_overlap(activities, settings.mode))
    issues.extend(_check_timezone(settings.timezone))
    issues.extend(_check_shift(settings.shift))
    issues.extend(_check_trim(settings.trim_start, settings.trim_end))
    issues.extend(_check_smooth_elevation(settings.smooth_elevation))
    issues.extend(_check_auto_lap(activities, settings.auto_lap_m))
    issues.extend(_check_lap_sources(settings.auto_lap_m, settings.detect_workout))
    issues.extend(_check_gpx_needs_position(activities, settings.output_format))
    if not settings.dry_run:  # a dry run writes nothing, so the output path is moot
        issues.extend(_check_output_path(settings.output, settings.assume_yes))
    return issues


def _check_gpx_needs_position(
    activities: list[Activity], output_format: str
) -> list[Issue]:
    """Flag a GPX write of recordings that have no position to store.

    GPX trackpoints require latitude and longitude and have no distance field, so
    an indoor (no-GPS) ride cannot be written as valid GPX. TCX can carry it.
    """
    if output_format != "gpx":
        return []
    if any(_has_position(activity) for activity in activities):
        return []
    return [
        Issue(
            EXIT_USAGE,
            "these recordings have no GPS position, and GPX cannot store a "
            "position-less activity (or its distance) -- write TCX instead with "
            "--format tcx",
        )
    ]


def _has_position(activity: Activity) -> bool:
    """Return whether any of an activity's points carries a position."""
    return any(p.lat is not None and p.lon is not None for p in activity.points())


def _check_timezone(timezone: str | None) -> list[Issue]:
    """Flag a timezone spec the preview cannot resolve to a local time."""
    if timezone is None:
        return []
    try:
        parse_timezone(timezone)
    except ValueError:
        return [
            Issue(
                EXIT_USAGE,
                f"could not understand timezone {timezone!r}; "
                "use e.g. 'Europe/Amsterdam' or '+02:00'",
            )
        ]
    return []


def _check_shift(shift: str | None) -> list[Issue]:
    """Flag a ``--shift`` duration the transform cannot parse."""
    if shift is None:
        return []
    try:
        parse_duration(shift)
    except ValueError:
        return [
            Issue(
                EXIT_USAGE,
                f"could not understand shift {shift!r}; "
                "use e.g. '+1h', '-30m', '90s', or '+1:30:00'",
            )
        ]
    return []


def _check_trim(trim_start: str | None, trim_end: str | None) -> list[Issue]:
    """Flag an unparseable trim bound, or a start that is not before the end."""
    issues: list[Issue] = []
    start = _parsed_bound(trim_start, "--trim-start", issues)
    end = _parsed_bound(trim_end, "--trim-end", issues)
    if start is not None and end is not None and start >= end:
        issues.append(
            Issue(
                EXIT_USAGE,
                f"--trim-start ({trim_start}) must be before --trim-end ({trim_end})",
            )
        )
    return issues


def _parsed_bound(value: str | None, flag: str, issues: list[Issue]) -> datetime | None:
    """Parse a trim bound, appending a usage issue (and returning None) if it fails."""
    if value is None:
        return None
    try:
        return parse_time(value)
    except ValueError:
        issues.append(
            Issue(
                EXIT_USAGE,
                f"could not understand {flag} {value!r}; use an ISO time like "
                "'2026-06-04T08:30:00Z'",
            )
        )
        return None


def _check_auto_lap(
    activities: list[Activity], auto_lap_m: float | None
) -> list[Issue]:
    """Flag an auto-lap request the inputs cannot satisfy.

    Re-lapping by distance needs a distance to measure against: either a recorded
    distance channel or positions to recompute one from. With neither, the
    transform would raise mid-pipeline, so it is caught here as a clean usage
    error up front.
    """
    if auto_lap_m is None:
        return []
    if any(_has_distance_or_position(activity) for activity in activities):
        return []
    return [
        Issue(
            EXIT_USAGE,
            "--auto-lap needs distance: these recordings have no recorded "
            "distance and no GPS position to measure one from, so they cannot be "
            "split into even laps",
        )
    ]


def _has_distance_or_position(activity: Activity) -> bool:
    """Return whether any point carries a recorded distance or a position."""
    return any(
        p.distance is not None or (p.lat is not None and p.lon is not None)
        for p in activity.points()
    )


def _check_lap_sources(auto_lap_m: float | None, detect_workout: bool) -> list[Issue]:
    """Flag setting both ``--auto-lap`` and ``--detect-workout``.

    Both write the activity's laps: auto-lap to even distance splits, workout
    detection to the detected intervals. Running both would mean one silently
    overwrites the other, so requiring exactly one is the honest contract.
    """
    if auto_lap_m is not None and detect_workout:
        return [
            Issue(
                EXIT_USAGE,
                "--auto-lap and --detect-workout both set laps; choose one",
            )
        ]
    return []


def _check_smooth_elevation(window: int | None) -> list[Issue]:
    """Flag a smoothing window that is not an odd integer of at least 3."""
    if window is None:
        return []
    if window < MIN_SMOOTH_WINDOW or window % 2 == 0:
        return [
            Issue(
                EXIT_USAGE,
                f"--smooth-elevation must be an odd integer >= {MIN_SMOOTH_WINDOW}, "
                f"got {window}",
            )
        ]
    return []


def _check_timestamps(activities: list[Activity]) -> list[Issue]:
    """Flag any activity that has no timestamps to order by."""
    issues: list[Issue] = []
    for activity in activities:
        if _span(activity) is None:
            issues.append(
                Issue(
                    EXIT_PARSE,
                    f"{_name(activity)} has no timestamps, so it cannot be "
                    f"merged (append orders by time; stack aligns on it)",
                )
            )
    return issues


def _check_mode_vs_overlap(activities: list[Activity], mode: str) -> list[Issue]:
    """Flag inputs whose time overlap does not match the chosen mode.

    Append expects sequential (non-overlapping) recordings; stack expects
    overlapping ones. ``"auto"`` is decided later from the same overlap, so it is
    not checked here.
    """
    overlapping = _overlaps(activities)
    if mode == "append" and overlapping:
        return [
            Issue(
                EXIT_MODE_CONFLICT,
                "input files overlap in time; for two recordings of the same ride "
                "use stack (--mode stack)",
            )
        ]
    if mode == "stack" and not overlapping:
        return [
            Issue(
                EXIT_MODE_CONFLICT,
                "input files do not overlap in time; for back-to-back recordings "
                "use append (--mode append)",
            )
        ]
    return []


def _overlaps(activities: list[Activity]) -> bool:
    """Return whether any two activities overlap in time (touching is allowed)."""
    spans = sorted(_require_span(a) for a in activities)
    return any(
        later_start < earlier_end
        for (_, earlier_end), (later_start, _) in zip(spans, spans[1:])
    )


def _check_output_path(output: Path, assume_yes: bool) -> list[Issue]:
    """Flag an output path we could not write to."""
    parent = output.parent if output.parent != Path("") else Path(".")
    if not parent.is_dir():
        return [Issue(EXIT_WRITE, f"output directory {parent} does not exist")]
    if not os.access(parent, os.W_OK):
        return [Issue(EXIT_WRITE, f"output directory {parent} is not writable")]
    if output.exists() and not assume_yes:
        return [
            Issue(
                EXIT_WRITE,
                f"{output} already exists; pass --yes to overwrite it",
            )
        ]
    return []


def _span(activity: Activity) -> tuple[datetime, datetime] | None:
    """Return an activity's (start, end) timestamps, or ``None`` if it has none."""
    times = [p.time for p in activity.points() if p.time is not None]
    if not times:
        return None
    return min(times), max(times)


def _require_span(activity: Activity) -> tuple[datetime, datetime]:
    """Return an activity's (start, end); only call once timestamps are confirmed."""
    span = _span(activity)
    assert span is not None  # guarded by _check_timestamps running first
    return span


def _name(activity: Activity) -> str:
    """Return a short label for an activity in messages."""
    if activity.source_path is not None:
        return Path(activity.source_path).name
    return activity.name or "an input file"
