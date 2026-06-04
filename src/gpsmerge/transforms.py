"""Optional adjustments applied to the single merged activity before it is written.

A merge produces one :class:`~gpsmerge.model.Activity`; these transforms tidy it
up afterwards. Each is a plain function that takes an activity and returns a new
one -- they never mutate the input, building fresh :class:`Segment` and
:class:`TrackPoint` objects with :func:`dataclasses.replace`, exactly as the merge
does. That keeps them composable: the pipeline chains them in a fixed order and
each sees the previous one's clean output.

The four adjustments and what each fixes:

- :func:`shift_time` -- the device clock was wrong, so every timestamp is off by a
  constant amount.
- :func:`trim` -- a warm-up or cool-down should be cut from the ends.
- :func:`smooth_elevation` -- a barometric altimeter is noisy and the elevation
  spikes need flattening.
- :func:`insert_stopped_time` -- a long non-moving gap should count as a pause
  (stopped time), not as moving time.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

from gpsmerge.model import Activity, Lap, Segment, TrackPoint

# A smoothing window must be odd (so it is centred on its point) and at least
# three wide (so it actually averages neighbours). The caller validates this; the
# guard here documents the contract for any direct caller.
_MIN_SMOOTH_WINDOW = 3


def shift_time(activity: Activity, offset: timedelta) -> Activity:
    """Return a copy of the activity with every timestamp moved by ``offset``.

    Use this to correct a device whose clock was wrong: a constant offset is added
    to each point's time. A point with no time is left untouched, and positions and
    every sensor channel are unchanged. Each lap's span is shifted by the same
    offset so the laps stay aligned with the points.

    Parameters
    ----------
    activity : Activity
        The activity to adjust. Not mutated.
    offset : timedelta
        The amount to add to each timestamp; may be negative.

    Returns
    -------
    Activity
        A new activity with shifted timestamps and everything else copied verbatim.
    """
    segments = [
        Segment(points=[_shifted(p, offset) for p in segment.points])
        for segment in activity.segments
    ]
    laps = [_shifted_lap(lap, offset) for lap in activity.laps]
    return replace(activity, segments=segments, laps=laps)


def _shifted(point: TrackPoint, offset: timedelta) -> TrackPoint:
    """Return a copy of ``point`` with its time moved by ``offset`` (None stays None)."""
    if point.time is None:
        return replace(point)
    return replace(point, time=point.time + offset)


def _shifted_lap(lap: Lap, offset: timedelta) -> Lap:
    """Return a copy of ``lap`` with its span moved by ``offset``.

    The lap is a span on the same clock as the points, so it shifts by the same
    constant. An open (``None``) end stays open.
    """
    end_time = None if lap.end_time is None else lap.end_time + offset
    return replace(lap, start_time=lap.start_time + offset, end_time=end_time)


def trim(activity: Activity, start: datetime | None, end: datetime | None) -> Activity:
    """Return a copy keeping only points whose time falls within ``[start, end]``.

    An open bound is unbounded on that side, so ``trim(a, start, None)`` keeps
    everything from ``start`` onward. A point with no time is dropped whenever
    either bound is set, because it cannot be placed in the window. Segments left
    empty by trimming are removed.

    After trimming, cumulative distance is re-based so it starts at zero again:
    where the kept points carry a distance, the first kept point's distance is
    subtracted from every point's distance (points with no distance are left
    ``None``). This keeps the written distance honest -- a trimmed track should read
    "0 km at the start", not inherit the offset of the points that were cut.

    Laps follow the same window: a lap that overlaps ``[start, end]`` is kept but
    clipped to the window (its start pulled up to ``start``, its end pushed down to
    ``end``), and a lap that falls entirely outside is dropped. An open bound does
    not clip on that side.

    Parameters
    ----------
    activity : Activity
        The activity to trim. Not mutated.
    start : datetime or None
        Keep points at or after this time; ``None`` leaves the start open.
    end : datetime or None
        Keep points at or before this time; ``None`` leaves the end open.

    Returns
    -------
    Activity
        A new activity holding only the in-range points, with distance re-based.
    """
    bounded = start is not None or end is not None
    segments: list[Segment] = []
    for segment in activity.segments:
        kept = [p for p in segment.points if _in_range(p, start, end, bounded)]
        if kept:
            segments.append(Segment(points=kept))

    _rebase_distance(segments)
    laps = [
        clipped for lap in activity.laps if (clipped := _clipped_lap(lap, start, end))
    ]
    return replace(activity, segments=segments, laps=laps)


def _in_range(
    point: TrackPoint, start: datetime | None, end: datetime | None, bounded: bool
) -> bool:
    """Return whether a point falls within the (possibly open) trim window."""
    if point.time is None:
        # With a bound set, an undated point cannot be placed, so it is dropped;
        # with no bound at all, trim keeps everything.
        return not bounded
    if start is not None and point.time < start:
        return False
    if end is not None and point.time > end:
        return False
    return True


def _clipped_lap(lap: Lap, start: datetime | None, end: datetime | None) -> Lap | None:
    """Clip a lap to the kept window, or return ``None`` if it falls outside it.

    The lap span ``[lap.start_time, lap.end_time]`` is intersected with the
    (possibly open) window ``[start, end]``. A ``None`` lap end means the lap runs
    to the activity's end, so it is treated as unbounded above; a ``None`` window
    bound is unbounded on that side and never clips. The lap survives only when the
    two spans actually overlap.
    """
    # No overlap if the lap ends before the window starts...
    if start is not None and lap.end_time is not None and lap.end_time < start:
        return None
    # ...or begins after the window ends.
    if end is not None and lap.start_time > end:
        return None

    new_start = lap.start_time if start is None else max(lap.start_time, start)
    new_end = _clipped_lap_end(lap.end_time, end)
    return replace(lap, start_time=new_start, end_time=new_end)


def _clipped_lap_end(lap_end: datetime | None, end: datetime | None) -> datetime | None:
    """Push a lap's end down to the window end, leaving an open side unbounded."""
    if end is None:
        # Open window end: the lap end stays as it was, including ``None``.
        return lap_end
    if lap_end is None:
        # Lap ran to the activity's end; the window now caps it at ``end``.
        return end
    return min(lap_end, end)


def _rebase_distance(segments: list[Segment]) -> None:
    """Subtract the first kept distance from every point's distance, in place.

    Operates on freshly built segments owned by :func:`trim`, so mutating them
    does not touch the caller's input. Points with no distance stay ``None``.
    """
    first = _first_distance(segments)
    if first is None:
        return
    for segment in segments:
        segment.points = [
            p if p.distance is None else replace(p, distance=p.distance - first)
            for p in segment.points
        ]


def _first_distance(segments: list[Segment]) -> float | None:
    """Return the first point's distance across the segments, or ``None`` if unset."""
    for segment in segments:
        for point in segment.points:
            if point.distance is not None:
                return point.distance
    return None


def smooth_elevation(activity: Activity, window: int) -> Activity:
    """Return a copy with each elevation replaced by a centred moving average.

    Each point's elevation becomes the mean of the elevations within
    ``+/- (window // 2)`` neighbours, counting only neighbours that have an
    elevation. A point whose own elevation is ``None`` stays ``None``. Positions,
    timestamps, and every sensor channel are unchanged. This tames the spikes a
    noisy barometric altimeter produces.

    Parameters
    ----------
    activity : Activity
        The activity to smooth. Not mutated.
    window : int
        The full window width. Must be an odd integer at least
        ``3`` (the caller validates this).

    Returns
    -------
    Activity
        A new activity with smoothed elevations.

    Raises
    ------
    ValueError
        If `window` is not an odd integer of at least 3.
    """
    if window < _MIN_SMOOTH_WINDOW or window % 2 == 0:
        raise ValueError(f"smoothing window must be an odd integer >= 3, got {window}")

    # Smooth across the whole point stream (segment boundaries do not break the
    # window), then drop each new elevation back in by walking the segments in the
    # same order, so the segment structure is preserved.
    elevations = [p.ele for p in activity.points()]
    half = window // 2
    new_eles = iter(_averaged(elevations, half))

    segments = [
        Segment(points=[_with_ele(p, next(new_eles)) for p in segment.points])
        for segment in activity.segments
    ]
    # Smoothing changes only elevation, not the timeline, so lap spans are unchanged.
    laps = [replace(lap) for lap in activity.laps]
    return replace(activity, segments=segments, laps=laps)


def _averaged(elevations: list[float | None], half: int) -> list[float | None]:
    """Return a centred moving average of ``elevations`` over ``+/- half`` neighbours.

    A ``None`` entry stays ``None``; otherwise each value is the mean of the
    non-``None`` neighbours in its window.
    """
    result: list[float | None] = []
    for index, ele in enumerate(elevations):
        if ele is None:
            result.append(None)
            continue
        window = [
            e
            for e in elevations[max(0, index - half) : index + half + 1]
            if e is not None
        ]
        result.append(sum(window) / len(window))
    return result


def _with_ele(point: TrackPoint, ele: float | None) -> TrackPoint:
    """Return a copy of ``point`` with its elevation set to ``ele``."""
    return replace(point, ele=ele)


def insert_stopped_time(activity: Activity, threshold_s: float) -> Activity:
    """Return a copy that splits each segment at every long time gap between points.

    Wherever two consecutive points within a segment are more than ``threshold_s``
    seconds apart, the gap becomes a new segment boundary -- a pause. The points
    themselves are unchanged; only the grouping into segments changes. Because the
    preview measures moving time within segments and excludes the gaps between
    them, a long non-moving gap then counts as stopped time rather than moving
    time.

    Parameters
    ----------
    activity : Activity
        The activity to re-segment. Not mutated.
    threshold_s : float
        A gap longer than this many seconds becomes a segment boundary.

    Returns
    -------
    Activity
        A new activity with the same points regrouped at the long gaps.
    """
    segments: list[Segment] = []
    for segment in activity.segments:
        segments.extend(_split_on_gaps(segment, threshold_s))
    # Re-segmenting only regroups points; lap spans over the timeline are unchanged.
    laps = [replace(lap) for lap in activity.laps]
    return replace(activity, segments=segments, laps=laps)


def _split_on_gaps(segment: Segment, threshold_s: float) -> list[Segment]:
    """Split one segment into runs broken wherever a time gap exceeds the threshold."""
    runs: list[list[TrackPoint]] = []
    current: list[TrackPoint] = []
    previous_time: datetime | None = None
    for point in segment.points:
        if (
            current
            and previous_time is not None
            and point.time is not None
            and (point.time - previous_time).total_seconds() > threshold_s
        ):
            runs.append(current)
            current = []
        current.append(replace(point))
        if point.time is not None:
            previous_time = point.time
    if current:
        runs.append(current)
    return [Segment(points=run) for run in runs]
