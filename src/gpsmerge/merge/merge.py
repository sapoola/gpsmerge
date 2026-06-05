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
from gpsmerge.merge import alignment
from gpsmerge.model import Activity, Device, Lap, Segment, TrackPoint
from gpsmerge.util.geo import haversine
from gpsmerge.util.resample import lerp

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
        One activity carrying every input's points in time order. Every defaulted
        field -- device, sport, name, and creation time -- is taken from the same
        input, the chronologically first activity, so attribution is consistent
        regardless of the order the inputs are passed. ``device_from`` and
        ``sport`` override their respective defaults.
    """
    ordered = sorted(activities, key=_sort_key)

    segments: list[Segment] = []
    laps: list[Lap] = []
    previous_end: datetime | None = None
    for activity in ordered:
        offset = _shift_offset(activity, previous_end, settings.gap_policy)
        for segment in activity.segments:
            segments.append(Segment(points=[_shift(p, offset) for p in segment.points]))
        # A lap is a span over this input's timeline, so it moves by the same
        # shift offset its points do; under "pause" the offset is zero and the
        # laps keep their recorded times.
        laps.extend(_shift_lap(lap, offset) for lap in activity.laps)
        previous_end = _last_time(segments)

    if settings.dedupe:
        _drop_seam_duplicates(segments)
    segments = [segment for segment in segments if segment.points]
    laps.sort(key=lambda lap: lap.start_time)

    if settings.recompute_distance:
        _assign_distance(_all_points(segments), settings.ignore_big_gaps_m)

    return Activity(
        sport=settings.sport or ordered[0].sport,
        name=ordered[0].name,
        device=_chosen_device(ordered, settings),
        segments=segments,
        laps=laps,
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


def _shift_lap(lap: Lap, offset: float) -> Lap:
    """Return a copy of ``lap`` with its span moved by ``offset`` seconds.

    Always copies, even when ``offset`` is zero, so the merge never mutates the
    input activities' laps. An open-ended lap (``end_time`` is ``None``) keeps its
    ``None`` end after shifting.
    """
    if offset == 0.0:
        return replace(lap)
    delta = timedelta(seconds=offset)
    end = lap.end_time + delta if lap.end_time is not None else None
    return replace(lap, start_time=lap.start_time + delta, end_time=end)


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
    # Indoor rides carry no position, so there is nothing to measure from: leave
    # the recorded distance the points already hold rather than zeroing it.
    if not any(p.lat is not None and p.lon is not None for p in points):
        return

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

    The user picks it with ``device_from``; by default it is the chronologically
    first activity (``ordered[0]``) -- the same input that supplies the defaulted
    sport, name, and creation time, so every defaulted field is attributed to one
    consistent input. The returned :class:`Device` is a copy so the merged
    activity never shares state with an input.
    """
    if settings.device_from is not None:
        found = _activity_by_path(ordered, settings.device_from)
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

    With ``clock_align`` set, each donor's clock offset relative to the master is
    detected first (see :mod:`gpsmerge.merge.alignment`) and its readings are shifted
    onto the master's clock before being borrowed, so a device whose clock is a
    few seconds off no longer smears its channel. The master is never shifted.

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
    aligned = _align_donors(activities, master, settings)
    donor_series = {
        channel: [
            aligned[id(owner)].channel_series(channel)
            for owner in _channel_owners(activities, settings, channel)
        ]
        for channel in SCALAR_DONOR_CHANNELS
    }

    segments: list[Segment] = []
    for segment in master.segments:
        points = [replace(p) for p in segment.points]  # keep time/position/elevation
        _fill_channels(points, donor_series, settings.max_gap_s)
        segments.append(Segment(points=points))

    if settings.recompute_distance:
        _assign_distance(_all_points(segments), settings.ignore_big_gaps_m)

    return Activity(
        sport=settings.sport or master.sport,
        name=master.name,
        device=_stack_device(activities, settings, master),
        segments=segments,
        # The master supplies the timeline, so its laps apply to the result
        # unchanged; copy them so the input is not shared. Donors contribute no
        # laps -- only borrowed sensor channels.
        laps=[replace(lap) for lap in master.laps],
        time_created=master.time_created,
    )


def _align_donors(
    activities: list[Activity], master: Activity, settings: Settings
) -> dict[int, Activity]:
    """Map each activity to the version stacking should read its channels from.

    The master always maps to itself: its timeline is the reference and is never
    moved. With ``clock_align`` off every donor also maps to itself, so stacking
    reads the donors exactly as recorded -- behaviour identical to before. With it
    on, each donor's clock offset relative to the master is detected and, when
    non-zero, the donor maps to a time-shifted *copy* (the inputs are never
    mutated) so its readings land on the master's clock before being borrowed.

    The mapping is keyed by ``id`` so the series builder can look up the right
    version per owner without changing how owners are chosen.
    """
    versions = {id(a): a for a in activities}
    if not settings.clock_align:
        return versions
    for donor in activities:
        if donor is master:
            continue
        offset = alignment.detect_clock_offset(master, donor)
        if offset != 0.0:
            versions[id(donor)] = _shift_activity(donor, offset)
    return versions


def _shift_activity(activity: Activity, offset: float) -> Activity:
    """Return a copy of ``activity`` with every point's time moved by ``offset`` s.

    Only the points are copied and re-timed (via the same per-point ``_shift``
    stacking trusts elsewhere); the rest of the activity is reused, because
    stacking reads only the shifted points' channels from the result.
    """
    segments = [
        Segment(points=[_shift(p, offset) for p in segment.points])
        for segment in activity.segments
    ]
    return replace(activity, segments=segments)


def _master(activities: list[Activity], settings: Settings) -> Activity:
    """Return the activity that provides position, time, and elevation.

    The user picks it with ``position_from``; by default it is the input with the
    most points carrying a position (the best GPS track). When no input has any
    position -- an indoor ride recorded on two sensors -- that count ties at zero,
    so we fall back to the input with the most points, which carries the richer
    timeline (and, typically, the recorded distance worth keeping).
    """
    if settings.position_from is not None:
        chosen = _activity_by_path(activities, settings.position_from)
        if chosen is not None:
            return chosen
    return max(activities, key=lambda a: (_position_point_count(a), _point_count(a)))


def _position_point_count(activity: Activity) -> int:
    """Return how many of an activity's points carry a latitude and longitude."""
    return sum(1 for p in activity.points() if p.lat is not None and p.lon is not None)


def _point_count(activity: Activity) -> int:
    """Return how many track points an activity has, for breaking master ties."""
    return sum(1 for _ in activity.points())


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


def _fill_channels(
    points: list[TrackPoint],
    donor_series: dict[str, list[list[tuple[float, float]]]],
    max_gap_s: float,
) -> None:
    """Resample every donor channel onto ``points`` in place, one owner at a time.

    For each channel the master target times are gathered once, then each owner is
    interpolated over the whole array with a single :func:`lerp` call -- rather than
    one call per point per owner, which made stacking O(n^2). Owners combine by the
    first-non-None-wins rule (a later owner fills only the gaps the earlier ones
    leave), matching the per-point fallback this replaced. Points without a time
    take no borrowed channels, so they are skipped here and keep the master's copy.
    """
    timed = [
        (i, p.time.timestamp()) for i, p in enumerate(points) if p.time is not None
    ]
    if not timed:
        return
    indices = [i for i, _ in timed]
    target_times = [t for _, t in timed]

    for channel, owner_series in donor_series.items():
        combined = _combine_owners(owner_series, target_times, max_gap_s)
        for index, value in zip(indices, combined):
            setattr(points[index], channel, _finalize(channel, value))


def _combine_owners(
    owner_series: list[list[tuple[float, float]]],
    target_times: list[float],
    max_gap_s: float,
) -> list[float | None]:
    """Interpolate each owner over all target times, first-non-None-wins per target.

    Each owner is resampled with one :func:`lerp` call across the whole timeline.
    The first owner supplies the baseline; each later owner fills only the targets
    the running result still leaves ``None``, so an earlier source always takes
    precedence -- the same ordering the per-point loop gave.
    """
    combined: list[float | None] = [None] * len(target_times)
    for series in owner_series:
        sampled = lerp(target_times, series, max_gap_s)
        for i, value in enumerate(sampled):
            if combined[i] is None:
                combined[i] = value
    return combined


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
