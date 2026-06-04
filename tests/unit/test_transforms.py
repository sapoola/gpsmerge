"""Tests for the post-merge transforms applied before writing."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from gpsmerge.model import Activity, Lap, Segment, TrackPoint
from gpsmerge.transforms import (
    insert_stopped_time,
    shift_time,
    smooth_elevation,
    trim,
)

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
