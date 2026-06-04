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

MIN_INPUTS_TO_MERGE = 2


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
    if not settings.dry_run:  # a dry run writes nothing, so the output path is moot
        issues.extend(_check_output_path(settings.output, settings.assume_yes))
    return issues


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
