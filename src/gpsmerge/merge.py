"""Merge several activities into one.

This module does the two merges that matter. :func:`append` joins recordings that
happened one after another (your bike computer died and you finished on your
phone) into a single continuous activity. :func:`stack` overlays recordings of the
same ride, taking a different sensor from each (GPS from a watch, power from a
trainer). :func:`detect_mode` guesses which one a set of files wants, for the
``auto`` mode.

The merge works only on the canonical :class:`~gpsmerge.model.Activity`; it never
touches GPX/TCX/FIT details. Readers have already turned files into the model and
a writer turns the result back into a file.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from gpsmerge.config import Settings
from gpsmerge.geo import haversine
from gpsmerge.model import Activity, Device, Segment, TrackPoint
from gpsmerge.resample import lerp

# Two points at a seam are "the same point" (and one is dropped) if they are this
# close in both time and space. A genuine next point is at least a sample apart in
# time, so these thresholds only ever catch a duplicated boundary record.
SEAM_MAX_TIME_S = 1.0
SEAM_MAX_DISTANCE_M = 5.0

# Sample spacing assumed at a seam when shifting and the file gives no better hint.
DEFAULT_NOMINAL_DT_S = 1.0

# Sorts activities with no timestamps last, without special-casing them.
_FAR_FUTURE = datetime.max.replace(tzinfo=UTC)

# Channels that stacking borrows from a chosen source onto the master track.
# Time, position, and elevation always come from the master (position is never
# interpolated), and distance is recomputed from positions -- so none are here.
SCALAR_DONOR_CHANNELS = ("hr", "cad", "power", "temp", "speed")

# Channels recorded as whole numbers: an interpolated value is rounded back.
INTEGER_CHANNELS = frozenset({"hr", "cad", "power"})

# The Settings field that names the source file for each user-selectable channel.
_CHANNEL_SOURCE_FIELD = {
    "hr": "hr_from",
    "cad": "cadence_from",
    "power": "power_from",
    "temp": "temp_from",
}

# Auto mode reads how much the recordings overlap, as a fraction of the shorter
# one. Above the high mark they are the same ride (stack); below the low mark they
# are sequential (append); in between, auto cannot decide.
STACK_OVERLAP_RATIO = 0.80
APPEND_OVERLAP_RATIO = 0.10


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
    """Return a copy of the device an appended file should be attributed to.

    The user picks it with ``device_from``; by default it is the first input
    file. The returned :class:`Device` is a copy so the merged activity never
    shares state with an input.
    """
    if settings.device_from is not None:
        found = _activity_by_path(ordered, settings.device_from)
        if found is not None:
            return replace(found.device)
    elif settings.inputs:
        found = _activity_by_path(ordered, settings.inputs[0])
        if found is not None:
            return replace(found.device)
    return replace(ordered[0].device)


def _activity_by_path(activities: list[Activity], path: Path) -> Activity | None:
    """Return the activity read from ``path``, matched by source path, or None."""
    wanted = str(path)
    for activity in activities:
        if activity.source_path == wanted:
            return activity
    return None


def stack(activities: list[Activity], settings: Settings) -> Activity:
    """Overlay recordings of the same ride, taking a different sensor from each.

    One input is the *master*: it provides the timeline, position, and elevation,
    copied verbatim -- position is never interpolated or invented. Each sensor
    channel (heart rate, cadence, power, temperature, speed) is taken from its
    chosen source and resampled onto the master's timestamps. Where a source has a
    gap longer than ``max_gap_s`` the channel is left blank rather than guessed,
    and when more than one source can supply a channel a later one fills the gaps
    the first leaves.

    Parameters
    ----------
    activities : list of Activity
        The overlapping recordings to combine.
    settings : Settings
        Merge options: the per-channel sources, ``max_gap_s``, device choice, and
        sport override.

    Returns
    -------
    Activity
        The master track enriched with the borrowed sensor channels.
    """
    master = _master(activities, settings)
    donor_series = {
        channel: [
            _series(owner, channel)
            for owner in _channel_owners(activities, settings, channel)
        ]
        for channel in SCALAR_DONOR_CHANNELS
    }

    segments: list[Segment] = []
    for segment in master.segments:
        points: list[TrackPoint] = []
        for source in segment.points:
            point = replace(source)  # keep the master's time, position, elevation
            if source.time is not None:
                seconds = source.time.timestamp()
                for channel in SCALAR_DONOR_CHANNELS:
                    sampled = _sample(
                        donor_series[channel], seconds, settings.max_gap_s
                    )
                    setattr(point, channel, _finalize(channel, sampled))
            points.append(point)
        segments.append(Segment(points=points))

    if settings.recompute_distance:
        _assign_distance(_all_points(segments), settings.ignore_big_gaps_m)

    return Activity(
        sport=settings.sport or master.sport,
        name=master.name,
        device=_stack_device(activities, settings, master),
        segments=segments,
        time_created=master.time_created,
    )


def _master(activities: list[Activity], settings: Settings) -> Activity:
    """Return the activity that provides position, time, and elevation.

    The user picks it with ``position_from``; by default it is the input with the
    most points carrying a position (the best GPS track).
    """
    if settings.position_from is not None:
        chosen = _activity_by_path(activities, settings.position_from)
        if chosen is not None:
            return chosen
    return max(activities, key=_position_point_count)


def _position_point_count(activity: Activity) -> int:
    """Return how many of an activity's points carry a latitude and longitude."""
    return sum(1 for p in activity.points() if p.lat is not None and p.lon is not None)


def _channel_owners(
    activities: list[Activity], settings: Settings, channel: str
) -> list[Activity]:
    """Return the activities that supply ``channel``, best first.

    If the user named a source for the channel, that is the only owner. Otherwise
    every activity that recorded the channel is an owner, in input order, so a
    later one can fill an earlier one's gaps.
    """
    field = _CHANNEL_SOURCE_FIELD.get(channel)
    chosen = getattr(settings, field) if field is not None else None
    if chosen is not None:
        owner = _activity_by_path(activities, chosen)
        return [owner] if owner is not None else []
    return [a for a in activities if _has_channel(a, channel)]


def _has_channel(activity: Activity, channel: str) -> bool:
    """Return whether an activity records any value for ``channel``."""
    return any(getattr(p, channel) is not None for p in activity.points())


def _series(activity: Activity, channel: str) -> list[tuple[float, float]]:
    """Return an activity's (time-seconds, value) samples for one channel, sorted."""
    samples = [
        (p.time.timestamp(), float(getattr(p, channel)))
        for p in activity.points()
        if p.time is not None and getattr(p, channel) is not None
    ]
    samples.sort()
    return samples


def _sample(
    owner_series: list[list[tuple[float, float]]], seconds: float, max_gap_s: float
) -> float | None:
    """Interpolate the first owner that has a value at ``seconds``, else None."""
    for series in owner_series:
        value = lerp([seconds], series, max_gap_s)[0]
        if value is not None:
            return value
    return None


def _finalize(channel: str, value: float | None) -> float | int | None:
    """Round an interpolated value back to an int for whole-number channels."""
    if value is None:
        return None
    if channel in INTEGER_CHANNELS:
        return int(round(value))
    return value


def _stack_device(
    activities: list[Activity], settings: Settings, master: Activity
) -> Device:
    """Return a copy of the device a stacked file is attributed to.

    The user picks it with ``device_from``; by default it is the master (the
    position source).
    """
    if settings.device_from is not None:
        chosen = _activity_by_path(activities, settings.device_from)
        if chosen is not None:
            return replace(chosen.device)
    return replace(master.device)


def detect_mode(activities: list[Activity]) -> str:
    """Guess whether recordings should be stacked, appended, or are ambiguous.

    Looks at how much the recordings overlap in time, as a fraction of the
    shorter one: a large overlap means the same ride recorded twice (stack), a
    tiny one means back-to-back recordings (append), and anything between is left
    for the user to decide.

    Parameters
    ----------
    activities : list of Activity
        The recordings to classify.

    Returns
    -------
    str
        ``"stack"``, ``"append"``, or ``"ambiguous"``.
    """
    spans = [_span(a) for a in activities]
    if len(activities) < 2 or any(span is None for span in spans):
        return "ambiguous"

    valid = [span for span in spans if span is not None]
    latest_start = max(start for start, _ in valid)
    earliest_end = min(end for _, end in valid)
    overlap = (earliest_end - latest_start).total_seconds()
    shortest = min((end - start).total_seconds() for start, end in valid)
    if shortest <= 0:
        return "ambiguous"

    ratio = max(0.0, overlap) / shortest
    if ratio > STACK_OVERLAP_RATIO:
        return "stack"
    if ratio < APPEND_OVERLAP_RATIO:
        return "append"
    return "ambiguous"


def _span(activity: Activity) -> tuple[datetime, datetime] | None:
    """Return an activity's (first, last) timestamps, or None if it has none."""
    times = [p.time for p in activity.points() if p.time is not None]
    if not times:
        return None
    return min(times), max(times)
