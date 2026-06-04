"""Tests for the append merge algorithm."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from gpsmerge.config import Settings
from gpsmerge.merge import append
from gpsmerge.model import Activity, Device, Segment, TrackPoint

A_PATH = Path("a.gpx")
B_PATH = Path("b.gpx")
_BASE = datetime(2026, 6, 4, 8, 0, 0, tzinfo=UTC)


def _point(offset: int, lat: float, lon: float, **channels: object) -> TrackPoint:
    """Build a point ``offset`` seconds after the base time."""
    return TrackPoint(
        time=_BASE + timedelta(seconds=offset), lat=lat, lon=lon, **channels
    )


def _activity(points: list[TrackPoint], *, device_name: str, source: Path) -> Activity:
    return Activity(
        sport="cycling",
        device=Device(name=device_name),
        segments=[Segment(points=points)],
        source_path=str(source),
    )


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {"inputs": [A_PATH, B_PATH], "output": Path("out.gpx")}
    base.update(overrides)
    return Settings.model_validate(base)


def _two_sequential() -> list[Activity]:
    first = _activity(
        [_point(0, 52.000, 4.000), _point(10, 52.001, 4.001)],
        device_name="Garmin Edge 530",
        source=A_PATH,
    )
    second = _activity(
        [_point(300, 52.002, 4.002), _point(310, 52.003, 4.003)],
        device_name="Stages Dash M50",
        source=B_PATH,
    )
    return [first, second]


@pytest.mark.unit
def test_append_orders_inputs_by_start_time() -> None:
    first, second = _two_sequential()
    # Pass them out of order; append should still place the earlier one first.
    merged = append([second, first], _settings())
    times = [p.time for p in merged.points()]
    assert times == sorted(times)


@pytest.mark.unit
def test_pause_keeps_real_timestamps_and_a_segment_boundary() -> None:
    merged = append(_two_sequential(), _settings(gap_policy="pause"))
    assert len(merged.segments) == 2
    # The 5-minute gap between recordings is preserved, not collapsed.
    assert [p.time.second for p in merged.points() if p.time] == [0, 10, 0, 10]


@pytest.mark.unit
def test_shift_moves_the_later_recording_to_be_continuous() -> None:
    merged = append(_two_sequential(), _settings(gap_policy="shift"))
    points = list(merged.points())
    # The second recording's first point lands one 10 s sample after the first ends.
    assert (points[2].time - points[1].time).total_seconds() == 10.0


@pytest.mark.unit
def test_dedupe_drops_a_duplicated_seam_point() -> None:
    first = _activity(
        [_point(0, 52.000, 4.000), _point(10, 52.001, 4.001)],
        device_name="A",
        source=A_PATH,
    )
    second = _activity(
        [
            _point(10, 52.001, 4.001),
            _point(20, 52.002, 4.002),
        ],  # first point duplicates
        device_name="B",
        source=B_PATH,
    )
    merged = append([first, second], _settings(dedupe=True))
    assert len(list(merged.points())) == 3


@pytest.mark.unit
def test_no_dedupe_keeps_the_duplicate() -> None:
    first = _activity(
        [_point(0, 52.0, 4.0), _point(10, 52.001, 4.001)],
        device_name="A",
        source=A_PATH,
    )
    second = _activity(
        [_point(10, 52.001, 4.001), _point(20, 52.002, 4.002)],
        device_name="B",
        source=B_PATH,
    )
    merged = append([first, second], _settings(dedupe=False))
    assert len(list(merged.points())) == 4


@pytest.mark.unit
def test_distance_is_recomputed_and_monotonic() -> None:
    merged = append(_two_sequential(), _settings(recompute_distance=True))
    distances = [p.distance for p in merged.points()]
    assert distances[0] == 0.0
    assert all(b >= a for a, b in zip(distances, distances[1:]))
    assert distances[-1] > 0.0


@pytest.mark.unit
def test_ignore_big_gaps_excludes_a_large_seam_jump() -> None:
    near = _activity(
        [_point(0, 52.0, 4.0), _point(10, 52.0001, 4.0001)],
        device_name="A",
        source=A_PATH,
    )
    far = _activity(
        [_point(300, 53.0, 5.0), _point(310, 53.0001, 5.0001)],
        device_name="B",
        source=B_PATH,
    )
    with_gap = append([near, far], _settings(ignore_big_gaps_m=None))
    without_gap = append([near, far], _settings(ignore_big_gaps_m=250.0))
    total_with = list(with_gap.points())[-1].distance
    total_without = list(without_gap.points())[-1].distance
    assert total_with is not None and total_without is not None
    # The ~130 km jump between the two recordings is excluded when ignored.
    assert total_with - total_without > 100_000


@pytest.mark.unit
def test_device_from_selects_that_input_device() -> None:
    merged = append(_two_sequential(), _settings(device_from=B_PATH))
    assert merged.device.name == "Stages Dash M50"


@pytest.mark.unit
def test_default_device_is_the_first_input() -> None:
    merged = append(_two_sequential(), _settings())
    assert merged.device.name == "Garmin Edge 530"


@pytest.mark.unit
def test_sport_override_is_applied() -> None:
    merged = append(_two_sequential(), _settings(sport="running"))
    assert merged.sport == "running"


@pytest.mark.unit
def test_shift_accumulates_across_three_files() -> None:
    a = _activity(
        [_point(0, 52.0, 4.0), _point(10, 52.001, 4.001)],
        device_name="A",
        source=A_PATH,
    )
    b = _activity(
        [_point(300, 52.002, 4.002), _point(310, 52.003, 4.003)],
        device_name="B",
        source=B_PATH,
    )
    c_path = Path("c.gpx")
    c = _activity(
        [_point(600, 52.004, 4.004), _point(610, 52.005, 4.005)],
        device_name="C",
        source=c_path,
    )
    merged = append(
        [a, b, c], _settings(inputs=[A_PATH, B_PATH, c_path], gap_policy="shift")
    )
    points = list(merged.points())
    deltas = [
        (later.time - earlier.time).total_seconds()
        for earlier, later in zip(points, points[1:])
    ]
    # Each recording starts one 10 s sample after the previous one ends: a continuous chain.
    assert deltas == [10.0, 10.0, 10.0, 10.0, 10.0]


@pytest.mark.unit
def test_append_keeps_recorded_distance_when_there_is_no_gps() -> None:
    # Indoor recordings carry no position; appending must keep their recorded
    # distance rather than recomputing it from (absent) positions to zero.
    a = _activity(
        [
            TrackPoint(time=_BASE, distance=0.0, power=100),
            TrackPoint(time=_BASE + timedelta(seconds=10), distance=50.0, power=110),
        ],
        device_name="A",
        source=A_PATH,
    )
    b = _activity(
        [
            TrackPoint(time=_BASE + timedelta(seconds=300), distance=0.0, power=120),
            TrackPoint(time=_BASE + timedelta(seconds=310), distance=70.0, power=130),
        ],
        device_name="B",
        source=B_PATH,
    )
    merged = append([a, b], _settings(recompute_distance=True))
    assert [p.distance for p in merged.points()] == [0.0, 50.0, 0.0, 70.0]


@pytest.mark.unit
def test_append_does_not_mutate_the_inputs() -> None:
    activities = _two_sequential()
    original_times = [p.time for p in activities[0].points()]
    original_distances = [p.distance for p in activities[0].points()]
    append(activities, _settings(gap_policy="shift", recompute_distance=True))
    assert [p.time for p in activities[0].points()] == original_times
    assert [p.distance for p in activities[0].points()] == original_distances
