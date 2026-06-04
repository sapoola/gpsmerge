"""Run one merge from start to finish: read, validate, merge, preview, write.

:func:`run` is the single entry point the command line (and later the wizard)
calls. It never raises for an expected problem; instead it returns a
:class:`RunResult` whose ``issues`` list is empty on success and otherwise
explains what went wrong and with which exit code. That keeps the caller simple:
print the issues, or print the preview, and exit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from gpsmerge.config import Settings
from gpsmerge.errors import (
    EXIT_EMPTY_RESULT,
    EXIT_MODE_CONFLICT,
    EXIT_PARSE,
    EXIT_WRITE,
    GpsmergeError,
    Issue,
)
from gpsmerge.geo import cumulative_distance, haversine
from gpsmerge.merge import append, detect_mode, stack
from gpsmerge.model import Activity, TrackPoint
from gpsmerge.read import read
from gpsmerge.timeutil import parse_timezone
from gpsmerge.validate import validate
from gpsmerge.write import write

# A position jump at least this large is called out in the preview as a likely
# gap, so a person sees it before trusting the merged distance.
SEAM_JUMP_WARN_M = 250.0


@dataclass(slots=True)
class Preview:
    """A human-readable summary of what a merge produced, shown before writing.

    Attributes
    ----------
    point_count : int
        Total number of track points in the result.
    moving_seconds : float
        Time spent recording within segments (pauses between segments excluded).
    elapsed_seconds : float
        Wall-clock time from the first point to the last.
    distance_m : float
        Total distance of the merged track, in metres.
    channels : list of str
        Which data channels are present (e.g. ``["position", "hr", "power"]``).
    device_name : str or None
        The device the merged file is attributed to.
    warnings : list of str
        Sanity warnings (such as a large unexplained position jump).
    start_time, end_time : datetime or None
        The first and last point's timestamps, in timezone-aware UTC.
    local_start, local_end : datetime or None
        ``start_time``/``end_time`` shown in the user's chosen timezone, for the
        preview only. ``None`` when no (valid) timezone was given. The written
        file's timestamps are unaffected and stay UTC.
    timezone_label : str or None
        The raw timezone spec the local times were resolved from, echoed back so
        the preview can name the zone.
    """

    point_count: int
    moving_seconds: float
    elapsed_seconds: float
    distance_m: float
    channels: list[str]
    device_name: str | None
    warnings: list[str] = field(default_factory=list)
    start_time: datetime | None = None
    end_time: datetime | None = None
    local_start: datetime | None = None
    local_end: datetime | None = None
    timezone_label: str | None = None


@dataclass(slots=True)
class RunResult:
    """The outcome of :func:`run`.

    Attributes
    ----------
    issues : list of Issue
        Empty on success; otherwise the blocking problems found.
    preview : Preview or None
        The summary of the merged activity, when one was produced.
    output : Path or None
        The path written (or that would be written, under ``dry_run``).
    written : bool
        Whether a file was actually written.
    """

    issues: list[Issue]
    preview: Preview | None = None
    output: Path | None = None
    written: bool = False


def run(settings: Settings) -> RunResult:
    """Read, validate, merge, and write according to ``settings``.

    Parameters
    ----------
    settings : Settings
        Everything the merge needs.

    Returns
    -------
    RunResult
        Success carries the preview and the written path; failure carries the
        issues and their exit codes.
    """
    try:
        activities = [read(path) for path in settings.inputs]
    except GpsmergeError as exc:
        return RunResult(issues=[Issue(EXIT_PARSE, str(exc))])

    issues = validate(activities, settings)
    if issues:
        return RunResult(issues=issues)

    mode: str = settings.mode
    if mode == "auto":
        mode = detect_mode(activities)
        if mode == "ambiguous":
            return RunResult(
                issues=[
                    Issue(
                        EXIT_MODE_CONFLICT,
                        "could not tell whether to stack or append; "
                        "pass --mode stack or --mode append",
                    )
                ]
            )

    merged = (
        stack(activities, settings) if mode == "stack" else append(activities, settings)
    )
    points = list(merged.points())
    if not points:
        return RunResult(
            issues=[Issue(EXIT_EMPTY_RESULT, "the merge produced no points")]
        )

    preview = _build_preview(merged, settings.timezone)

    if settings.dry_run:
        return RunResult(
            issues=[], preview=preview, output=settings.output, written=False
        )

    try:
        write(merged, settings.output, settings.output_format)
    except OSError as exc:
        return RunResult(
            issues=[Issue(EXIT_WRITE, f"could not write {settings.output}: {exc}")],
            preview=preview,
        )

    return RunResult(issues=[], preview=preview, output=settings.output, written=True)


def _build_preview(activity: Activity, timezone: str | None) -> Preview:
    """Summarise a merged activity, including any sanity warnings.

    Parameters
    ----------
    activity : Activity
        The merged activity to describe.
    timezone : str or None
        A timezone spec for local display only (see
        :func:`gpsmerge.timeutil.parse_timezone`). When given and valid, the
        preview also carries the start/end shown in that zone; the activity's
        own timestamps are never touched.
    """
    points = list(activity.points())
    start, end = _time_span(points)
    local_start, local_end = _local_span(start, end, timezone)
    return Preview(
        point_count=len(points),
        moving_seconds=_moving_seconds(activity),
        elapsed_seconds=_elapsed_seconds(points),
        distance_m=_total_distance(points),
        channels=_channels(points),
        device_name=activity.device.name,
        warnings=_warnings(points),
        start_time=start,
        end_time=end,
        local_start=local_start,
        local_end=local_end,
        timezone_label=timezone if local_start is not None else None,
    )


def _time_span(
    points: list[TrackPoint],
) -> tuple[datetime | None, datetime | None]:
    """Return the first and last timestamps (UTC), or ``(None, None)`` if none."""
    times = [p.time for p in points if p.time is not None]
    if not times:
        return None, None
    return min(times), max(times)


def _local_span(
    start: datetime | None, end: datetime | None, timezone: str | None
) -> tuple[datetime | None, datetime | None]:
    """Convert a UTC span into the chosen timezone, for display only.

    Returns ``(None, None)`` when no timezone is given, when there are no times,
    or when the spec does not resolve -- validation surfaces a bad spec as an
    error, so here it simply yields no local times rather than raising.
    """
    if timezone is None or start is None or end is None:
        return None, None
    try:
        zone = parse_timezone(timezone)
    except ValueError:
        return None, None
    return start.astimezone(zone), end.astimezone(zone)


def _moving_seconds(activity: Activity) -> float:
    """Sum the recorded time within each segment, excluding pauses between them."""
    total = 0.0
    for segment in activity.segments:
        times = [p.time for p in segment.points if p.time is not None]
        if len(times) >= 2:
            total += (times[-1] - times[0]).total_seconds()
    return total


def _elapsed_seconds(points: list[TrackPoint]) -> float:
    """Return wall-clock seconds from the first timestamp to the last."""
    times = [p.time for p in points if p.time is not None]
    if len(times) < 2:
        return 0.0
    return (max(times) - min(times)).total_seconds()


def _total_distance(points: list[TrackPoint]) -> float:
    """Return the total distance, preferring an assigned value, else recomputing."""
    last = points[-1].distance if points else None
    if last is not None:
        return last
    distances = cumulative_distance(points)
    return distances[-1] if distances else 0.0


def _channels(points: list[TrackPoint]) -> list[str]:
    """List the data channels present anywhere in the points, in a stable order."""
    present = []
    if any(p.lat is not None and p.lon is not None for p in points):
        present.append("position")
    checks = [
        ("elevation", "ele"),
        ("hr", "hr"),
        ("cadence", "cad"),
        ("power", "power"),
        ("temperature", "temp"),
        ("speed", "speed"),
    ]
    for label, attr in checks:
        if any(getattr(p, attr) is not None for p in points):
            present.append(label)
    return present


def _warnings(points: list[TrackPoint]) -> list[str]:
    """Return sanity warnings -- currently large unexplained position jumps."""
    warnings: list[str] = []
    for earlier, later in zip(points, points[1:]):
        if earlier.lat is None or earlier.lon is None:
            continue
        if later.lat is None or later.lon is None:
            continue
        jump = haversine(earlier.lat, earlier.lon, later.lat, later.lon)
        if jump >= SEAM_JUMP_WARN_M:
            where = later.time.isoformat() if later.time else "an unknown time"
            warnings.append(
                f"position jumps {jump / 1000:.1f} km at {where} -- looks like a "
                f"gap; pass --ignore-gaps-over-meters to keep it out of the distance"
            )
    return warnings
