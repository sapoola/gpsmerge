"""Tests for the post-merge transforms applied before writing."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest

from gpsmerge.errors import TransformError
from gpsmerge.model import Activity, Lap, Segment, TrackPoint
from gpsmerge.transforms import (
    auto_lap,
    insert_stopped_time,
    laps_from_boundaries,
    shift_time,
    smooth_elevation,
    trim,
)
from gpsmerge.util.geo import EARTH_RADIUS_M

_BASE = datetime(2026, 6, 4, 8, 0, 0, tzinfo=UTC)


def _at(offset: int, **fields: object) -> TrackPoint:
    """Build a point ``offset`` seconds after the base time, with extra fields."""
    return TrackPoint(time=_BASE + timedelta(seconds=offset), **fields)  # type: ignore[arg-type]


def _lap(start: int, end: int | None, trigger: str | None = None) -> Lap:
    """Build a lap spanning ``start``..``end`` seconds after the base time.

    An ``end`` of ``None`` makes an open-ended lap that runs to the activity end.
    """
    end_time = None if end is None else _BASE + timedelta(seconds=end)
    return Lap(
        start_time=_BASE + timedelta(seconds=start),
        end_time=end_time,
        trigger=trigger,
    )


def _activity(*segments: list[TrackPoint]) -> Activity:
    """Build an activity from one list of points per segment."""
    return Activity(segments=[Segment(points=list(seg)) for seg in segments])


def _lap_offsets(activity: Activity) -> list[tuple[int, int | None]]:
    """Return each lap's (start, end) as integer seconds after the base time."""
    return [
        (
            int((lap.start_time - _BASE).total_seconds()),
            (
                None
                if lap.end_time is None
                else int((lap.end_time - _BASE).total_seconds())
            ),
        )
        for lap in activity.laps
    ]


# --- shift_time ----------------------------------------------------------


@pytest.mark.unit
def test_shift_time_moves_every_timestamp_by_the_offset() -> None:
    activity = _activity([_at(0), _at(10)])

    shifted = shift_time(activity, timedelta(hours=1))

    times = [p.time for p in shifted.points()]
    assert times == [_BASE + timedelta(hours=1), _BASE + timedelta(hours=1, seconds=10)]


@pytest.mark.unit
def test_shift_time_leaves_positions_and_channels_untouched() -> None:
    activity = _activity([_at(0, lat=52.0, lon=4.0, hr=120)])

    point = next(shift_time(activity, timedelta(seconds=5)).points())

    assert (point.lat, point.lon, point.hr) == (52.0, 4.0, 120)


@pytest.mark.unit
def test_shift_time_leaves_a_timeless_point_as_is() -> None:
    activity = _activity([TrackPoint(time=None, lat=52.0, lon=4.0)])

    point = next(shift_time(activity, timedelta(hours=1)).points())

    assert point.time is None


@pytest.mark.unit
def test_shift_time_does_not_mutate_the_input() -> None:
    activity = _activity([_at(0)])

    shift_time(activity, timedelta(hours=1))

    assert next(activity.points()).time == _BASE


@pytest.mark.unit
def test_shift_time_moves_a_lap_span_by_the_offset() -> None:
    activity = _activity([_at(0), _at(30)])
    activity.laps = [_lap(0, 30, trigger="manual")]

    shifted = shift_time(activity, timedelta(seconds=5))

    assert _lap_offsets(shifted) == [(5, 35)]
    assert shifted.laps[0].trigger == "manual"


@pytest.mark.unit
def test_shift_time_keeps_an_open_ended_lap_open() -> None:
    activity = _activity([_at(0), _at(30)])
    activity.laps = [_lap(0, None)]

    shifted = shift_time(activity, timedelta(seconds=5))

    assert _lap_offsets(shifted) == [(5, None)]


@pytest.mark.unit
def test_shift_time_does_not_mutate_the_input_laps() -> None:
    activity = _activity([_at(0)])
    activity.laps = [_lap(0, 30)]

    shift_time(activity, timedelta(hours=1))

    assert _lap_offsets(activity) == [(0, 30)]


@pytest.mark.unit
def test_shift_time_leaves_an_activity_without_laps_unchanged() -> None:
    activity = _activity([_at(0), _at(10)])

    shifted = shift_time(activity, timedelta(hours=1))

    assert shifted.laps == []


# --- trim ----------------------------------------------------------------


@pytest.mark.unit
def test_trim_keeps_only_points_within_the_window() -> None:
    activity = _activity([_at(0), _at(10), _at(20), _at(30)])

    trimmed = trim(
        activity, _BASE + timedelta(seconds=10), _BASE + timedelta(seconds=20)
    )

    offsets = [int((p.time - _BASE).total_seconds()) for p in trimmed.points()]  # type: ignore[operator]
    assert offsets == [10, 20]


@pytest.mark.unit
def test_trim_drops_a_segment_left_empty() -> None:
    activity = _activity([_at(0), _at(10)], [_at(100), _at(110)])

    trimmed = trim(activity, None, _BASE + timedelta(seconds=20))

    assert len(trimmed.segments) == 1


@pytest.mark.unit
def test_trim_re_bases_distance_to_start_at_zero() -> None:
    activity = _activity(
        [
            _at(0, distance=0.0),
            _at(10, distance=100.0),
            _at(20, distance=250.0),
            _at(30, distance=400.0),
        ]
    )

    trimmed = trim(
        activity, _BASE + timedelta(seconds=10), _BASE + timedelta(seconds=30)
    )

    distances = [p.distance for p in trimmed.points()]
    # First kept point carried 100 m; everything is re-based so it now reads 0.
    assert distances == [0.0, 150.0, 300.0]


@pytest.mark.unit
def test_trim_leaves_distance_none_when_points_have_none() -> None:
    activity = _activity([_at(0), _at(10), _at(20)])

    trimmed = trim(activity, _BASE + timedelta(seconds=10), None)

    assert all(p.distance is None for p in trimmed.points())


@pytest.mark.unit
def test_trim_with_a_bound_drops_a_timeless_point() -> None:
    activity = _activity([TrackPoint(time=None, lat=52.0, lon=4.0), _at(10)])

    trimmed = trim(activity, _BASE, None)

    assert [p.time for p in trimmed.points()] == [_BASE + timedelta(seconds=10)]


@pytest.mark.unit
def test_trim_does_not_mutate_the_input() -> None:
    activity = _activity([_at(0, distance=100.0), _at(10, distance=200.0)])

    trim(activity, _BASE + timedelta(seconds=10), None)

    assert [p.distance for p in activity.points()] == [100.0, 200.0]


@pytest.mark.unit
def test_trim_drops_a_lap_fully_before_the_window() -> None:
    activity = _activity([_at(0), _at(50)])
    activity.laps = [_lap(0, 10), _lap(20, 40)]

    trimmed = trim(activity, _BASE + timedelta(seconds=20), None)

    # The first lap (0..10) ends before the window starts, so it is dropped.
    assert _lap_offsets(trimmed) == [(20, 40)]


@pytest.mark.unit
def test_trim_drops_a_lap_fully_after_the_window() -> None:
    activity = _activity([_at(0), _at(50)])
    activity.laps = [_lap(0, 10), _lap(30, 40)]

    trimmed = trim(activity, None, _BASE + timedelta(seconds=20))

    # The second lap (30..40) starts after the window ends, so it is dropped.
    assert _lap_offsets(trimmed) == [(0, 10)]


@pytest.mark.unit
def test_trim_clips_a_lap_straddling_the_window_start() -> None:
    activity = _activity([_at(0), _at(50)])
    activity.laps = [_lap(5, 30)]

    trimmed = trim(activity, _BASE + timedelta(seconds=10), None)

    # The lap began at 5, before the window start of 10, so its start is pulled up.
    assert _lap_offsets(trimmed) == [(10, 30)]


@pytest.mark.unit
def test_trim_clips_a_lap_straddling_the_window_end() -> None:
    activity = _activity([_at(0), _at(50)])
    activity.laps = [_lap(10, 40)]

    trimmed = trim(activity, None, _BASE + timedelta(seconds=30))

    # The lap ran to 40, past the window end of 30, so its end is pushed down.
    assert _lap_offsets(trimmed) == [(10, 30)]


@pytest.mark.unit
def test_trim_caps_an_open_ended_lap_at_the_window_end() -> None:
    activity = _activity([_at(0), _at(50)])
    activity.laps = [_lap(10, None)]

    trimmed = trim(activity, None, _BASE + timedelta(seconds=30))

    # An open-ended lap ran to the activity end; the window now caps it at 30.
    assert _lap_offsets(trimmed) == [(10, 30)]


@pytest.mark.unit
def test_trim_keeps_a_lap_wholly_inside_the_window_untouched() -> None:
    activity = _activity([_at(0), _at(50)])
    activity.laps = [_lap(15, 25, trigger="distance")]

    trimmed = trim(
        activity, _BASE + timedelta(seconds=10), _BASE + timedelta(seconds=30)
    )

    assert _lap_offsets(trimmed) == [(15, 25)]
    assert trimmed.laps[0].trigger == "distance"


@pytest.mark.unit
def test_trim_does_not_mutate_the_input_laps() -> None:
    activity = _activity([_at(0), _at(50)])
    activity.laps = [_lap(5, 30)]

    trim(activity, _BASE + timedelta(seconds=10), _BASE + timedelta(seconds=20))

    assert _lap_offsets(activity) == [(5, 30)]


@pytest.mark.unit
def test_trim_leaves_an_activity_without_laps_unchanged() -> None:
    activity = _activity([_at(0), _at(10), _at(20)])

    trimmed = trim(activity, _BASE + timedelta(seconds=10), None)

    assert trimmed.laps == []


# --- smooth_elevation ----------------------------------------------------


@pytest.mark.unit
def test_smooth_elevation_averages_a_spike_down() -> None:
    activity = _activity([_at(0, ele=10.0), _at(10, ele=100.0), _at(20, ele=10.0)])

    smoothed = smooth_elevation(activity, window=3)

    eles = [p.ele for p in smoothed.points()]
    # The 100 m spike, averaged with its 10 m neighbours, drops to 40.
    assert eles[1] == pytest.approx(40.0)


@pytest.mark.unit
def test_smooth_elevation_leaves_a_none_elevation_none() -> None:
    activity = _activity([_at(0, ele=10.0), _at(10), _at(20, ele=30.0)])

    smoothed = smooth_elevation(activity, window=3)

    assert list(smoothed.points())[1].ele is None


@pytest.mark.unit
def test_smooth_elevation_ignores_none_neighbours_in_the_average() -> None:
    activity = _activity([_at(0, ele=10.0), _at(10), _at(20, ele=30.0)])

    smoothed = smooth_elevation(activity, window=3)

    eles = list(smoothed.points())
    # The middle point has no elevation, so the first point averages only itself
    # and the (None-skipped) neighbour -> stays 10.0.
    assert eles[0].ele == pytest.approx(10.0)
    assert eles[2].ele == pytest.approx(30.0)


@pytest.mark.unit
def test_smooth_elevation_leaves_other_channels_untouched() -> None:
    activity = _activity([_at(0, ele=10.0, lat=52.0, lon=4.0, hr=120)])

    point = next(smooth_elevation(activity, window=3).points())

    assert (point.lat, point.lon, point.hr) == (52.0, 4.0, 120)


@pytest.mark.unit
def test_smooth_elevation_rejects_an_even_window() -> None:
    activity = _activity([_at(0, ele=10.0)])
    with pytest.raises(ValueError):
        smooth_elevation(activity, window=4)


@pytest.mark.unit
def test_smooth_elevation_rejects_a_window_below_three() -> None:
    activity = _activity([_at(0, ele=10.0)])
    with pytest.raises(ValueError):
        smooth_elevation(activity, window=1)


@pytest.mark.unit
def test_smooth_elevation_does_not_mutate_the_input() -> None:
    activity = _activity([_at(0, ele=10.0), _at(10, ele=100.0), _at(20, ele=10.0)])

    smooth_elevation(activity, window=3)

    assert [p.ele for p in activity.points()] == [10.0, 100.0, 10.0]


@pytest.mark.unit
def test_smooth_elevation_carries_laps_through_unchanged() -> None:
    activity = _activity([_at(0, ele=10.0), _at(10, ele=100.0), _at(20, ele=10.0)])
    activity.laps = [_lap(0, 10, trigger="time"), _lap(10, None)]

    smoothed = smooth_elevation(activity, window=3)

    assert _lap_offsets(smoothed) == [(0, 10), (10, None)]
    assert smoothed.laps[0].trigger == "time"


@pytest.mark.unit
def test_smooth_elevation_does_not_mutate_the_input_laps() -> None:
    activity = _activity([_at(0, ele=10.0), _at(10, ele=100.0)])
    original = _lap(0, 10)
    activity.laps = [original]

    smoothed = smooth_elevation(activity, window=3)

    # A fresh Lap object is built, leaving the caller's lap untouched.
    assert smoothed.laps[0] is not original
    assert _lap_offsets(activity) == [(0, 10)]


# --- insert_stopped_time -------------------------------------------------


@pytest.mark.unit
def test_insert_stopped_time_splits_at_a_gap_over_the_threshold() -> None:
    # A 60 s gap between the second and third point exceeds a 30 s threshold.
    activity = _activity([_at(0), _at(10), _at(70), _at(80)])

    split = insert_stopped_time(activity, threshold_s=30.0)

    assert len(split.segments) == 2
    assert len(split.segments[0].points) == 2
    assert len(split.segments[1].points) == 2


@pytest.mark.unit
def test_insert_stopped_time_does_not_split_a_gap_within_the_threshold() -> None:
    activity = _activity([_at(0), _at(10), _at(20), _at(30)])

    split = insert_stopped_time(activity, threshold_s=30.0)

    assert len(split.segments) == 1


@pytest.mark.unit
def test_insert_stopped_time_keeps_the_points_unchanged() -> None:
    activity = _activity([_at(0, lat=52.0), _at(70, lat=53.0)])

    split = insert_stopped_time(activity, threshold_s=30.0)

    lats = [p.lat for p in split.points()]
    assert lats == [52.0, 53.0]


@pytest.mark.unit
def test_insert_stopped_time_does_not_mutate_the_input() -> None:
    activity = _activity([_at(0), _at(70)])

    insert_stopped_time(activity, threshold_s=30.0)

    assert len(activity.segments) == 1


@pytest.mark.unit
def test_insert_stopped_time_carries_laps_through_unchanged() -> None:
    # The 60 s gap splits the segment, but the lap spanning it is left as-is: a
    # pause is not a lap boundary.
    activity = _activity([_at(0), _at(10), _at(70), _at(80)])
    activity.laps = [_lap(0, 80, trigger="manual")]

    split = insert_stopped_time(activity, threshold_s=30.0)

    assert len(split.segments) == 2
    assert _lap_offsets(split) == [(0, 80)]
    assert split.laps[0].trigger == "manual"


@pytest.mark.unit
def test_insert_stopped_time_does_not_mutate_the_input_laps() -> None:
    activity = _activity([_at(0), _at(70)])
    original = _lap(0, 70)
    activity.laps = [original]

    split = insert_stopped_time(activity, threshold_s=30.0)

    assert split.laps[0] is not original
    assert _lap_offsets(activity) == [(0, 70)]


@pytest.mark.unit
def test_insert_stopped_time_leaves_an_activity_without_laps_unchanged() -> None:
    activity = _activity([_at(0), _at(70)])

    split = insert_stopped_time(activity, threshold_s=30.0)

    assert split.laps == []


@pytest.mark.unit
def test_smooth_elevation_window_crosses_a_segment_boundary() -> None:
    # Smoothing runs across the whole point stream, so the window pulls in the
    # neighbour on the other side of a pause (segment) boundary.
    activity = _activity(
        [_at(0, ele=10.0), _at(10, ele=10.0)],
        [_at(20, ele=100.0), _at(30, ele=10.0)],
    )

    smoothed = smooth_elevation(activity, window=3)

    assert len(smoothed.segments) == 2  # segment structure is preserved
    eles = [p.ele for p in smoothed.points()]
    # Segment 1's last point averages with segment 2's first point (100) -> 40.
    assert eles[1] == pytest.approx(40.0)


# --- auto_lap ------------------------------------------------------------

# A degree of latitude is this many metres along a meridian (same fixed sphere
# the geo helpers use), so points one degree apart in latitude are this far apart.
_METRES_PER_DEGREE_LATITUDE = EARTH_RADIUS_M * math.pi / 180.0


def _lap_triggers(activity: Activity) -> list[str | None]:
    """Return each lap's trigger in order."""
    return [lap.trigger for lap in activity.laps]


@pytest.mark.unit
def test_auto_lap_splits_on_even_distance_boundaries() -> None:
    # Six points at 0, 400, 800, ... 2000 m, one per 10 s. A 1000 m interval
    # should close a lap at the first point reaching 1000 m (1200 m, t=30) and at
    # the first reaching 2000 m (2000 m, t=50), leaving no leftover.
    points = [_at(i * 10, distance=i * 400.0) for i in range(6)]
    activity = _activity(points)

    relapped = auto_lap(activity, interval_m=1000.0)

    assert _lap_offsets(relapped) == [(0, 30), (30, 50)]
    assert _lap_triggers(relapped) == ["distance", "distance"]


@pytest.mark.unit
def test_auto_lap_final_partial_lap_is_session_end() -> None:
    # Points to 2500 m: the 1 km and 2 km marks close distance laps, and the
    # leftover 2000..2500 m is the final session-end lap.
    points = [_at(i * 10, distance=i * 500.0) for i in range(6)]  # 0..2500 m
    activity = _activity(points)

    relapped = auto_lap(activity, interval_m=1000.0)

    # 1000 m first reached at i=2 (t=20); 2000 m at i=4 (t=40); end at t=50.
    assert _lap_offsets(relapped) == [(0, 20), (20, 40), (40, 50)]
    assert _lap_triggers(relapped) == ["distance", "distance", "session_end"]


@pytest.mark.unit
def test_auto_lap_shorter_than_one_interval_is_a_single_lap() -> None:
    # Total distance 600 m never reaches the 1 km mark: one whole-activity lap.
    points = [_at(0, distance=0.0), _at(10, distance=300.0), _at(20, distance=600.0)]
    activity = _activity(points)

    relapped = auto_lap(activity, interval_m=1000.0)

    assert _lap_offsets(relapped) == [(0, 20)]
    assert _lap_triggers(relapped) == ["session_end"]


@pytest.mark.unit
def test_auto_lap_replaces_existing_laps() -> None:
    points = [_at(i * 10, distance=i * 500.0) for i in range(5)]  # 0..2000 m
    activity = _activity(points)
    activity.laps = [_lap(0, 40, trigger="manual")]  # a stale source lap

    relapped = auto_lap(activity, interval_m=1000.0)

    # The manual lap is gone, replaced by the two 1 km distance laps.
    assert _lap_triggers(relapped) == ["distance", "distance"]
    assert all(lap.trigger != "manual" for lap in relapped.laps)


@pytest.mark.unit
def test_auto_lap_uses_recorded_distance_when_present() -> None:
    # The recorded distance jumps fast while positions barely move; the laps must
    # follow the recorded distance, not a position recompute.
    points = [
        _at(0, lat=0.0, lon=0.0, distance=0.0),
        _at(10, lat=0.0001, lon=0.0, distance=600.0),
        _at(20, lat=0.0002, lon=0.0, distance=1200.0),
    ]
    activity = _activity(points)

    relapped = auto_lap(activity, interval_m=1000.0)

    # 1000 m first reached at the third point (1200 m, t=20); no leftover beyond.
    assert _lap_offsets(relapped) == [(0, 20)]
    assert _lap_triggers(relapped) == ["distance"]


@pytest.mark.unit
def test_auto_lap_falls_back_to_haversine_without_recorded_distance() -> None:
    # No recorded distance: distance is recomputed from positions. Points are one
    # degree of latitude apart (~111.19 km each), so a 200 km interval first
    # closes at the second step (~222 km).
    points = [
        _at(0, lat=0.0, lon=0.0),
        _at(10, lat=1.0, lon=0.0),
        _at(20, lat=2.0, lon=0.0),
    ]
    activity = _activity(points)

    relapped = auto_lap(activity, interval_m=200_000.0)

    # ~111 km at t=10 (below 200 km), ~222 km at t=20 crosses 200 km.
    assert _lap_offsets(relapped) == [(0, 20)]


@pytest.mark.unit
def test_auto_lap_falls_back_when_recorded_distance_goes_backwards() -> None:
    # A non-monotonic recorded distance is not trusted; the position recompute is
    # used instead. Positions are one degree of latitude apart each.
    points = [
        _at(0, lat=0.0, lon=0.0, distance=0.0),
        _at(10, lat=1.0, lon=0.0, distance=50.0),  # backwards vs next
        _at(20, lat=2.0, lon=0.0, distance=10.0),  # decreases: not monotonic
    ]
    activity = _activity(points)

    relapped = auto_lap(activity, interval_m=200_000.0)

    # Falling back to haversine (~111 km/step) crosses 200 km at the last point.
    assert _lap_offsets(relapped) == [(0, 20)]


@pytest.mark.unit
def test_auto_lap_does_not_mutate_the_input() -> None:
    points = [_at(i * 10, distance=i * 500.0) for i in range(5)]
    activity = _activity(points)
    activity.laps = [_lap(0, 40, trigger="manual")]

    auto_lap(activity, interval_m=1000.0)

    # The original keeps its single manual lap; a fresh activity was returned.
    assert _lap_triggers(activity) == ["manual"]


@pytest.mark.unit
def test_auto_lap_without_distance_or_position_raises() -> None:
    # No recorded distance and no positions: there is nothing to measure laps by.
    points = [_at(0, hr=120), _at(10, hr=130), _at(20, hr=140)]
    activity = _activity(points)

    with pytest.raises(TransformError):
        auto_lap(activity, interval_m=1000.0)


@pytest.mark.unit
def test_auto_lap_with_no_timed_points_raises() -> None:
    activity = _activity([TrackPoint(time=None, distance=500.0)])

    with pytest.raises(TransformError):
        auto_lap(activity, interval_m=1000.0)


@pytest.mark.unit
def test_auto_lap_rejects_a_non_positive_interval() -> None:
    activity = _activity([_at(0, distance=0.0), _at(10, distance=500.0)])

    with pytest.raises(ValueError):
        auto_lap(activity, interval_m=0.0)


# --- laps_from_boundaries (the shared helper) ----------------------------


@pytest.mark.unit
def test_laps_from_boundaries_pairs_consecutive_starts() -> None:
    end = _BASE + timedelta(seconds=60)
    boundaries = [
        _BASE,
        _BASE + timedelta(seconds=20),
        _BASE + timedelta(seconds=40),
    ]

    laps = laps_from_boundaries(boundaries, activity_end=end, trigger="distance")

    spans = []
    for lap in laps:
        assert lap.end_time is not None  # these laps are all closed
        spans.append(
            (
                int((lap.start_time - _BASE).total_seconds()),
                int((lap.end_time - _BASE).total_seconds()),
            )
        )
    assert spans == [(0, 20), (20, 40), (40, 60)]
    assert [lap.trigger for lap in laps] == ["distance", "distance", "session_end"]


@pytest.mark.unit
def test_laps_from_boundaries_single_start_is_one_whole_lap() -> None:
    end = _BASE + timedelta(seconds=30)

    laps = laps_from_boundaries([_BASE], activity_end=end, trigger="distance")

    assert len(laps) == 1
    assert laps[0].start_time == _BASE
    assert laps[0].end_time == end
    assert laps[0].trigger == "session_end"


@pytest.mark.unit
def test_laps_from_boundaries_no_trailing_lap_when_last_boundary_is_the_end() -> None:
    end = _BASE + timedelta(seconds=40)
    boundaries = [_BASE, _BASE + timedelta(seconds=20), end]

    laps = laps_from_boundaries(boundaries, activity_end=end, trigger="distance")

    # The last boundary lands exactly on the end: no zero-length session-end lap.
    assert [lap.trigger for lap in laps] == ["distance", "distance"]
    assert laps[-1].end_time == end


@pytest.mark.unit
def test_laps_from_boundaries_empty_yields_no_laps() -> None:
    assert laps_from_boundaries([], activity_end=_BASE, trigger="distance") == []


@pytest.mark.unit
def test_laps_from_boundaries_honours_the_completed_trigger() -> None:
    # The detector phase reuses this with its own trigger; it must be passed through.
    end = _BASE + timedelta(seconds=40)
    boundaries = [_BASE, _BASE + timedelta(seconds=20)]

    laps = laps_from_boundaries(boundaries, activity_end=end, trigger="time")

    assert laps[0].trigger == "time"
    assert laps[-1].trigger == "session_end"
