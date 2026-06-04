"""Merge several activities into one.

Today this module does one job: :func:`append`, which joins recordings that
happened one after another (your bike computer died and you finished on your
phone) into a single continuous activity. Stacking -- overlaying two recordings
of the same ride -- arrives in a later milestone and will live alongside it here.

The merge works only on the canonical :class:`~gpsmerge.model.Activity`; it never
touches GPX/TCX/FIT details. Readers have already turned files into the model and
a writer turns the result back into a file.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from gpsmerge.config import Settings
from gpsmerge.geo import haversine
from gpsmerge.model import Activity, Device, Segment, TrackPoint

# Two points at a seam are "the same point" (and one is dropped) if they are this
# close in both time and space. A genuine next point is at least a sample apart in
# time, so these thresholds only ever catch a duplicated boundary record.
SEAM_MAX_TIME_S = 1.0
SEAM_MAX_DISTANCE_M = 5.0

# Sample spacing assumed at a seam when shifting and the file gives no better hint.
DEFAULT_NOMINAL_DT_S = 1.0

# Sorts activities with no timestamps last, without special-casing them.
_FAR_FUTURE = datetime.max.replace(tzinfo=UTC)


def append(activities: list[Activity], settings: Settings) -> Activity:
    """Join sequential activities end-to-end into one activity.

    The activities are ordered by start time and concatenated. Each input keeps
    its own segment boundaries, and the join between two inputs is itself a
    segment boundary, so a pause between recordings is preserved rather than
    drawn as a straight line. With ``gap_policy="shift"`` the later recordings
    are moved in time to start right after the earlier one; with ``"pause"`` (the
    default) the real timestamps are kept.

    Parameters
    ----------
    activities : list of Activity
        The recordings to join. Expected to be sequential (non-overlapping);
        callers should validate that first.
    settings : Settings
        Merge options: gap policy, dedupe, distance recompute, device choice,
        and sport override.

    Returns
    -------
    Activity
        One activity carrying every input's points in time order, attributed to
        the chosen recording device.
    """
    ordered = sorted(activities, key=_sort_key)

    segments: list[Segment] = []
    previous_end: datetime | None = None
    for activity in ordered:
        offset = _shift_offset(activity, previous_end, settings.gap_policy)
        for segment in activity.segments:
            segments.append(Segment(points=[_shift(p, offset) for p in segment.points]))
        previous_end = _last_time(segments)

    if settings.dedupe:
        _drop_seam_duplicates(segments)
    segments = [segment for segment in segments if segment.points]

    if settings.recompute_distance:
        _assign_distance(_all_points(segments), settings.ignore_big_gaps_m)

    return Activity(
        sport=settings.sport or ordered[0].sport,
        name=ordered[0].name,
        device=_chosen_device(ordered, settings),
        segments=segments,
        time_created=ordered[0].time_created,
    )


def _sort_key(activity: Activity) -> datetime:
    """Return an activity's start time for ordering; far-future if it has none."""
    return _start_time(activity) or _FAR_FUTURE


def _start_time(activity: Activity) -> datetime | None:
    """Return the earliest timestamp in an activity, or ``None`` if it has none."""
    times = [p.time for p in activity.points() if p.time is not None]
    return min(times) if times else None


def _shift_offset(
    activity: Activity, previous_end: datetime | None, gap_policy: str
) -> float:
    """Return the seconds to add to ``activity`` so it follows the previous one.

    Zero under the ``"pause"`` policy (timestamps are kept as recorded) and for
    the first activity. Under ``"shift"`` it places this activity's first point
    one sample after the previous activity's last point.
    """
    if gap_policy != "shift" or previous_end is None:
        return 0.0
    start = _start_time(activity)
    if start is None:
        return 0.0
    return (previous_end.timestamp() + _nominal_dt(activity)) - start.timestamp()


def _nominal_dt(activity: Activity) -> float:
    """Estimate the activity's sample spacing in seconds from its first two points."""
    times = [p.time for p in activity.points() if p.time is not None]
    if len(times) < 2:
        return DEFAULT_NOMINAL_DT_S
    return (times[1] - times[0]).total_seconds() or DEFAULT_NOMINAL_DT_S


def _shift(point: TrackPoint, offset: float) -> TrackPoint:
    """Return a copy of ``point`` with its time moved by ``offset`` seconds.

    Always copies, even when ``offset`` is zero, so the merge never mutates the
    input activities (distance is later assigned onto these copies).
    """
    if offset == 0.0 or point.time is None:
        return replace(point)
    return replace(point, time=point.time + timedelta(seconds=offset))


def _last_time(segments: list[Segment]) -> datetime | None:
    """Return the last recorded timestamp across the built segments so far."""
    for segment in reversed(segments):
        for point in reversed(segment.points):
            if point.time is not None:
                return point.time
    return None


def _drop_seam_duplicates(segments: list[Segment]) -> None:
    """Drop a segment's first point when it duplicates the previous segment's last.

    This catches the doubled record some devices write at the boundary where two
    recordings meet. A real pause within an activity is far enough apart in time
    not to match, so only true duplicates are removed.
    """
    for previous, current in zip(segments, segments[1:]):
        if (
            previous.points
            and current.points
            and _is_duplicate(previous.points[-1], current.points[0])
        ):
            current.points.pop(0)


def _is_duplicate(a: TrackPoint, b: TrackPoint) -> bool:
    """Return whether two points are the same instant and place within thresholds."""
    if a.time is None or b.time is None:
        return False
    if abs((a.time - b.time).total_seconds()) > SEAM_MAX_TIME_S:
        return False
    if a.lat is None or a.lon is None or b.lat is None or b.lon is None:
        return False
    return haversine(a.lat, a.lon, b.lat, b.lon) <= SEAM_MAX_DISTANCE_M


def _all_points(segments: list[Segment]) -> list[TrackPoint]:
    """Return every point across the segments, in order."""
    return [point for segment in segments for point in segment.points]


def _assign_distance(points: list[TrackPoint], ignore_big_gaps_m: float | None) -> None:
    """Set each point's cumulative distance from positions, skipping big jumps.

    Distance runs continuously across segment boundaries (a pause does not lose
    the distance already covered). A step longer than ``ignore_big_gaps_m`` -- a
    coffee-stop gap or a GPS glitch -- is not added to the total, so it cannot
    inflate the distance, though the measurement resumes from the new position.
    """
    running = 0.0
    last_lat: float | None = None
    last_lon: float | None = None
    for point in points:
        if point.lat is not None and point.lon is not None:
            if last_lat is not None and last_lon is not None:
                step = haversine(last_lat, last_lon, point.lat, point.lon)
                if ignore_big_gaps_m is None or step <= ignore_big_gaps_m:
                    running += step
            last_lat, last_lon = point.lat, point.lon
        point.distance = running


def _chosen_device(ordered: list[Activity], settings: Settings) -> Device:
    """Return a copy of the device the merged file should be attributed to.

    The user picks it with ``device_from``; by default it is the first input
    file. The returned :class:`Device` is a copy so the merged activity never
    shares state with an input.
    """
    target = settings.device_from or (settings.inputs[0] if settings.inputs else None)
    if target is not None:
        wanted = str(target)
        for activity in ordered:
            if activity.source_path == wanted:
                return replace(activity.device)
    return replace(ordered[0].device)
