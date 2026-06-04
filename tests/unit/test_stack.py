"""Tests for the stack merge algorithm.

Stacking overlays recordings of the same ride and takes a different sensor from
each: the master supplies the verbatim timeline, position, and elevation, and
every borrowed channel is interpolated onto the master's timestamps. These tests
build tiny in-code activities and assert that behaviour, not the internals.
"""

from __future__ import annotations

import math
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from gpsmerge.config import Settings
from gpsmerge.merge import stack
from gpsmerge.model import Activity, Device, Lap, Segment, TrackPoint

WATCH_PATH = Path("watch.gpx")
TRAINER_PATH = Path("trainer.gpx")
_BASE = datetime(2026, 6, 4, 8, 0, 0, tzinfo=UTC)

# The trainer never sees the sky, so it records position 0/0 as a placeholder.
# Stacking must take its position from the master (the watch), never from this.
PLACEHOLDER_LAT = 0.0
PLACEHOLDER_LON = 0.0


def _point(
    offset: int, lat: float | None, lon: float | None, **channels: object
) -> TrackPoint:
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
    base: dict[str, object] = {
        "inputs": [WATCH_PATH, TRAINER_PATH],
        "output": Path("out.gpx"),
        "mode": "stack",
    }
    base.update(overrides)
    return Settings.model_validate(base)


def _watch() -> Activity:
    """A GPS watch over 0/10/20 s: real position, elevation, and heart rate."""
    return _activity(
        [
            _point(0, 52.000, 4.000, ele=10.0, hr=120),
            _point(10, 52.001, 4.001, ele=11.0, hr=130),
            _point(20, 52.002, 4.002, ele=12.0, hr=140),
        ],
        device_name="Garmin Fenix",
        source=WATCH_PATH,
    )


def _trainer() -> Activity:
    """An indoor trainer over 0/10/20 s: power and cadence, placeholder position."""
    return _activity(
        [
            _point(0, PLACEHOLDER_LAT, PLACEHOLDER_LON, power=200, cad=80),
            _point(10, PLACEHOLDER_LAT, PLACEHOLDER_LON, power=210, cad=82),
            _point(20, PLACEHOLDER_LAT, PLACEHOLDER_LON, power=220, cad=84),
        ],
        device_name="Wahoo Kickr",
        source=TRAINER_PATH,
    )


@pytest.mark.unit
def test_position_time_and_elevation_come_from_the_master_verbatim() -> None:
    watch, trainer = _watch(), _trainer()
    merged = stack([watch, trainer], _settings(position_from=WATCH_PATH))
    points = list(merged.points())
    # Every output position is the watch's, never the trainer's 0/0 placeholder.
    assert [(p.lat, p.lon) for p in points] == [
        (52.000, 4.000),
        (52.001, 4.001),
        (52.002, 4.002),
    ]
    assert [p.ele for p in points] == [10.0, 11.0, 12.0]
    assert [p.time for p in points] == [p.time for p in watch.points()]
    # The borrowed channels came from the trainer.
    assert [p.power for p in points] == [200, 210, 220]
    assert [p.cad for p in points] == [80, 82, 84]


@pytest.mark.unit
def test_default_master_is_the_input_with_more_positioned_points() -> None:
    watch = _watch()
    # An indoor trainer with no GPS at all: fewer positioned points than the watch.
    trainer = _activity(
        [
            _point(0, None, None, power=200),
            _point(10, None, None, power=210),
            _point(20, None, None, power=220),
        ],
        device_name="Wahoo Kickr",
        source=TRAINER_PATH,
    )
    # No position_from: the GPS-rich watch wins over the position-less trainer,
    # even when the trainer is given first.
    merged = stack([trainer, watch], _settings())
    assert [(p.lat, p.lon) for p in merged.points()] == [
        (52.000, 4.000),
        (52.001, 4.001),
        (52.002, 4.002),
    ]
    assert merged.device.name == "Garmin Fenix"  # the master is the watch


@pytest.mark.unit
def test_a_borrowed_channel_is_interpolated_onto_the_master_timestamps() -> None:
    # Master records at 0 and 10 s; donor is sampled at 5 s steps.
    master = _activity(
        [_point(0, 52.0, 4.0), _point(10, 52.001, 4.001)],
        device_name="Master",
        source=WATCH_PATH,
    )
    donor = _activity(
        [
            _point(0, 0.0, 0.0, power=100),
            _point(5, 0.0, 0.0, power=150),
            _point(10, 0.0, 0.0, power=200),
        ],
        device_name="Donor",
        source=TRAINER_PATH,
    )
    merged = stack([master, donor], _settings(position_from=WATCH_PATH))
    # Donor's value at the master's instants: 100 at t=0, 200 at t=10.
    assert [p.power for p in merged.points()] == [100, 200]


@pytest.mark.unit
def test_a_donor_gap_longer_than_max_gap_leaves_the_channel_none() -> None:
    master = _activity(
        [_point(0, 52.0, 4.0), _point(60, 52.001, 4.001)],
        device_name="Master",
        source=WATCH_PATH,
    )
    # Donor jumps from t=0 to t=60: a 60 s bracket, wider than max_gap_s=10.
    donor = _activity(
        [_point(0, 0.0, 0.0, power=100), _point(60, 0.0, 0.0, power=200)],
        device_name="Donor",
        source=TRAINER_PATH,
    )
    merged = stack([master, donor], _settings(position_from=WATCH_PATH, max_gap_s=10.0))
    powers = [p.power for p in merged.points()]
    # t=0 hits the donor's first sample exactly; t=60 hits the last sample.
    # Neither is fabricated across the gap -- the endpoints are real readings.
    assert powers == [100, 200]
    # A master point inside the gap gets nothing: the channel stays None there.
    midpoint_master = _activity(
        [_point(0, 52.0, 4.0), _point(30, 52.0005, 4.0005), _point(60, 52.001, 4.001)],
        device_name="Master",
        source=WATCH_PATH,
    )
    merged_mid = stack(
        [midpoint_master, donor], _settings(position_from=WATCH_PATH, max_gap_s=10.0)
    )
    assert [p.power for p in merged_mid.points()] == [100, None, 200]


@pytest.mark.unit
def test_integer_channels_are_ints_after_interpolation() -> None:
    master = _activity(
        [_point(0, 52.0, 4.0), _point(10, 52.001, 4.001)],
        device_name="Master",
        source=WATCH_PATH,
    )
    # Halfway between hr 120 and 130 is 125; the t=5 master point lands there.
    donor = _activity(
        [_point(0, 0.0, 0.0, hr=120), _point(10, 0.0, 0.0, hr=130)],
        device_name="Donor",
        source=TRAINER_PATH,
    )
    middled = _activity(
        [_point(0, 52.0, 4.0), _point(5, 52.0005, 4.0005), _point(10, 52.001, 4.001)],
        device_name="Master",
        source=WATCH_PATH,
    )
    merged = stack([middled, donor], _settings(position_from=WATCH_PATH))
    middle_hr = list(merged.points())[1].hr
    assert middle_hr == 125
    assert isinstance(middle_hr, int)
    # Avoid the unused-binding warning while keeping master available for clarity.
    assert master.device.name == "Master"


@pytest.mark.unit
def test_multi_donor_fallback_fills_the_first_sources_gap() -> None:
    master = _activity(
        [_point(0, 52.0, 4.0), _point(10, 52.001, 4.001), _point(20, 52.002, 4.002)],
        device_name="Master",
        source=WATCH_PATH,
    )
    # First hr source has a hole at t=10 (no sample there, gap too wide to fill).
    first = _activity(
        [_point(0, 0.0, 0.0, hr=100), _point(20, 0.0, 0.0, hr=120)],
        device_name="First",
        source=TRAINER_PATH,
    )
    # Second hr source covers the hole at t=10.
    second_path = Path("second.gpx")
    second = _activity(
        [
            _point(0, 0.0, 0.0, hr=150),
            _point(10, 0.0, 0.0, hr=160),
            _point(20, 0.0, 0.0, hr=170),
        ],
        device_name="Second",
        source=second_path,
    )
    merged = stack(
        [master, first, second],
        _settings(
            inputs=[WATCH_PATH, TRAINER_PATH, second_path],
            position_from=WATCH_PATH,
            max_gap_s=10.0,
        ),
    )
    hrs = [p.hr for p in merged.points()]
    # t=0 and t=20 come from the first source; t=10, where the first has a gap,
    # is filled by the second source's reading (160), not left None.
    assert hrs == [100, 160, 120]


@pytest.mark.unit
def test_power_from_forces_a_single_source() -> None:
    master = _activity(
        [_point(0, 52.0, 4.0), _point(10, 52.001, 4.001)],
        device_name="Master",
        source=WATCH_PATH,
    )
    chosen = _activity(
        [_point(0, 0.0, 0.0, power=100), _point(10, 0.0, 0.0, power=110)],
        device_name="Chosen",
        source=TRAINER_PATH,
    )
    # Another input also has power, but --power-from names the chosen one only.
    other_path = Path("other.gpx")
    other = _activity(
        [_point(0, 0.0, 0.0, power=900), _point(10, 0.0, 0.0, power=999)],
        device_name="Other",
        source=other_path,
    )
    merged = stack(
        [master, chosen, other],
        _settings(
            inputs=[WATCH_PATH, TRAINER_PATH, other_path],
            position_from=WATCH_PATH,
            power_from=TRAINER_PATH,
        ),
    )
    # Only the chosen source's power appears; the other's 900/999 is ignored.
    assert [p.power for p in merged.points()] == [100, 110]


@pytest.mark.unit
def test_device_defaults_to_the_master() -> None:
    watch, trainer = _watch(), _trainer()
    merged = stack([watch, trainer], _settings(position_from=WATCH_PATH))
    assert merged.device.name == "Garmin Fenix"  # the master (position source)


@pytest.mark.unit
def test_device_from_overrides_the_master_device() -> None:
    watch, trainer = _watch(), _trainer()
    merged = stack(
        [watch, trainer], _settings(position_from=WATCH_PATH, device_from=TRAINER_PATH)
    )
    assert merged.device.name == "Wahoo Kickr"  # the donor, by explicit choice


@pytest.mark.unit
def test_indoor_stack_keeps_the_masters_recorded_distance() -> None:
    # Two indoor recordings with no position at all, but each carrying the
    # distance its device measured (a Stages FIT plus a Garmin Edge TCX, say).
    master = _activity(
        [
            _point(0, None, None, distance=0.0, hr=120),
            _point(10, None, None, distance=50.0, hr=130),
            _point(20, None, None, distance=110.0, hr=140),
        ],
        device_name="Garmin Edge",
        source=WATCH_PATH,
    )
    donor = _activity(
        [
            _point(0, None, None, distance=0.0, power=200, cad=80),
            _point(10, None, None, distance=999.0, power=210, cad=82),
            _point(20, None, None, distance=999.0, power=220, cad=84),
        ],
        device_name="Stages",
        source=TRAINER_PATH,
    )
    merged = stack([master, donor], _settings(position_from=WATCH_PATH))
    # Stacking borrowed power/cadence, so it did something useful...
    assert [p.power for p in merged.points()] == [200, 210, 220]
    assert [p.cad for p in merged.points()] == [80, 82, 84]
    # ...and the master's recorded distance survived, not zeroed by recompute.
    assert [p.distance for p in merged.points()] == [0.0, 50.0, 110.0]


@pytest.mark.unit
def test_stack_recomputes_distance_when_positions_exist() -> None:
    # With real positions, distance is still recomputed from them, replacing
    # whatever the points happened to carry.
    watch = _activity(
        [
            _point(0, 52.000, 4.000, distance=7.0, hr=120),
            _point(10, 52.001, 4.001, distance=7.0, hr=130),
            _point(20, 52.002, 4.002, distance=7.0, hr=140),
        ],
        device_name="Garmin Fenix",
        source=WATCH_PATH,
    )
    trainer = _trainer()
    merged = stack([watch, trainer], _settings(position_from=WATCH_PATH))
    distances = [p.distance for p in merged.points()]
    # The stale 7.0 values are overwritten: distance starts at 0 and grows.
    assert distances[0] == 0.0
    assert all(b >= a for a, b in zip(distances, distances[1:]))
    assert distances[-1] > 0.0


@pytest.mark.unit
def test_stack_does_not_mutate_the_inputs() -> None:
    watch, trainer = _watch(), _trainer()
    watch_positions = [(p.lat, p.lon, p.ele) for p in watch.points()]
    watch_hr = [p.hr for p in watch.points()]
    trainer_power = [p.power for p in trainer.points()]
    trainer_positions = [(p.lat, p.lon) for p in trainer.points()]

    stack([watch, trainer], _settings(position_from=WATCH_PATH))

    assert [(p.lat, p.lon, p.ele) for p in watch.points()] == watch_positions
    assert [p.hr for p in watch.points()] == watch_hr
    assert [p.power for p in trainer.points()] == trainer_power
    assert [(p.lat, p.lon) for p in trainer.points()] == trainer_positions
    # The watch never gained the trainer's borrowed channels.
    assert all(p.power is None for p in watch.points())


@pytest.mark.unit
def test_indoor_master_is_the_input_with_more_points() -> None:
    # No file has GPS, so the position-count ties at zero; the data-richer file
    # (more points) becomes master and supplies the timeline and recorded distance.
    sparse = _activity(
        [_point(0, None, None, hr=120), _point(20, None, None, hr=130)],
        device_name="Edge 530",
        source=WATCH_PATH,
    )
    rich = _activity(
        [
            _point(0, None, None, power=200, distance=0.0),
            _point(10, None, None, power=210, distance=50.0),
            _point(20, None, None, power=220, distance=110.0),
        ],
        device_name="Stages",
        source=TRAINER_PATH,
    )

    merged = stack([sparse, rich], _settings())

    assert merged.device.name == "Stages"  # the richer file is master
    assert len(list(merged.points())) == 3  # its timeline
    # Its recorded distance is kept (there is no GPS to recompute from).
    assert [p.distance for p in merged.points()] == [0.0, 50.0, 110.0]


@pytest.mark.unit
def test_stack_with_no_laps_produces_no_laps() -> None:
    # Neither the master nor the donor carries laps, so neither does the merge.
    watch, trainer = _watch(), _trainer()
    merged = stack([watch, trainer], _settings(position_from=WATCH_PATH))
    assert merged.laps == []


@pytest.mark.unit
def test_stack_laps_come_from_the_master_only() -> None:
    # The master supplies the timeline, so its laps apply; the donor's laps,
    # which describe a different timeline, are ignored.
    watch, trainer = _watch(), _trainer()
    master_lap = Lap(
        start_time=_BASE, end_time=_BASE + timedelta(seconds=20), trigger="manual"
    )
    watch.laps = [master_lap]
    trainer.laps = [
        Lap(
            start_time=_BASE + timedelta(seconds=5),
            end_time=_BASE + timedelta(seconds=15),
            trigger="distance",
        )
    ]
    merged = stack([watch, trainer], _settings(position_from=WATCH_PATH))
    assert [(lap.start_time, lap.end_time, lap.trigger) for lap in merged.laps] == [
        (_BASE, _BASE + timedelta(seconds=20), "manual")
    ]


@pytest.mark.unit
def test_stack_does_not_mutate_the_masters_laps() -> None:
    watch, trainer = _watch(), _trainer()
    watch.laps = [
        Lap(start_time=_BASE, end_time=_BASE + timedelta(seconds=20), trigger="manual")
    ]
    lap_before = replace(watch.laps[0])
    stack([watch, trainer], _settings(position_from=WATCH_PATH))
    assert watch.laps[0] == lap_before


@pytest.mark.unit
def test_large_stack_completes_quickly() -> None:
    # The old per-point/per-owner/per-channel lerp made this O(n^2): a 20k-point
    # stack took many seconds. Vectorised, it must finish well under a second.
    n = 20_000
    master = _activity(
        [_point(i, 52.0 + i * 1e-6, 4.0 + i * 1e-6) for i in range(n)],
        device_name="Master",
        source=WATCH_PATH,
    )
    donor = _activity(
        [_point(i, 0.0, 0.0, power=100 + (i % 50)) for i in range(n)],
        device_name="Donor",
        source=TRAINER_PATH,
    )

    started = time.perf_counter()
    merged = stack([master, donor], _settings(position_from=WATCH_PATH))
    elapsed = time.perf_counter() - started

    assert len(list(merged.points())) == n
    assert elapsed < 1.0, f"20k-point stack took {elapsed:.2f}s, expected < 1s"


# --- clock-skew alignment ------------------------------------------------

# How many seconds the donor's clock runs fast in the alignment fixtures: it
# stamps every reading this much later than the moment it actually recorded it.
_SKEW_S = 5


def _speed(second: int) -> float:
    """A clearly varying speed shape so cross-correlation can lock onto a lag."""
    return 5.0 + 3.0 * math.sin(second / 6.0)


def _aligned_master() -> Activity:
    """A GPS master carrying a varying speed shape, at true 1 Hz over ~60 s."""
    return _activity(
        [
            _point(s, 52.0 + s * 1e-5, 4.0 + s * 1e-5, speed=_speed(s))
            for s in range(61)
        ],
        device_name="Master",
        source=WATCH_PATH,
    )


def _skewed_donor() -> Activity:
    """A donor whose clock is ``_SKEW_S`` s fast, carrying speed and power.

    The donor shares the master's speed shape (so the offset is detectable) and
    carries power equal to its true second times ten. Every timestamp is stamped
    ``_SKEW_S`` seconds late, so the reading the donor labels ``t`` is really the
    value from ``t - _SKEW_S``.
    """
    return _activity(
        [
            _point(
                s + _SKEW_S,
                PLACEHOLDER_LAT,
                PLACEHOLDER_LON,
                speed=_speed(s),
                power=s * 10,
            )
            for s in range(61)
        ],
        device_name="Donor",
        source=TRAINER_PATH,
    )


def _power_at(merged: Activity, master_second: int) -> int:
    """Return the merged power at the master point ``master_second`` seconds in."""
    target = _BASE + timedelta(seconds=master_second)
    for point in merged.points():
        if point.time == target:
            assert point.power is not None, f"no borrowed power at +{master_second}s"
            return point.power
    raise AssertionError(f"no master point at +{master_second}s")


@pytest.mark.unit
def test_clock_align_lines_the_borrowed_channel_up_with_the_donors_true_value() -> None:
    master, donor = _aligned_master(), _skewed_donor()
    # The donor's true power at second 30 is 300. Without correcting the skew the
    # donor's stamp at master-time 30 holds the value from its true second 25.
    settings = _settings(position_from=WATCH_PATH, clock_align=True, max_gap_s=2.0)

    merged = stack([master, donor], settings)

    # With the +5 s skew corrected, master-time 30 carries the donor's true
    # second-30 reading (300), not the second-25 reading it would borrow raw.
    assert _power_at(merged, 30) == 300


@pytest.mark.unit
def test_clock_align_beats_leaving_the_skew_in() -> None:
    master = _aligned_master()
    donor = _skewed_donor()
    aligned = stack(
        [master, donor],
        _settings(position_from=WATCH_PATH, clock_align=True, max_gap_s=2.0),
    )
    raw = stack(
        [master, donor],
        _settings(position_from=WATCH_PATH, clock_align=False, max_gap_s=2.0),
    )

    true_power = 30 * 10  # the donor's real power at master-time 30
    aligned_error = abs(_power_at(aligned, 30) - true_power)
    raw_error = abs(_power_at(raw, 30) - true_power)
    # Correcting the clock lands closer to the donor's real reading than leaving
    # the 5 s skew in does.
    assert aligned_error < raw_error


@pytest.mark.unit
def test_clock_align_defaults_off_and_changes_nothing() -> None:
    master, donor = _aligned_master(), _skewed_donor()
    # The default settings have clock_align off, so the borrowed channel is taken
    # exactly as the explicit clock_align=False case does.
    default = stack([master, donor], _settings(position_from=WATCH_PATH, max_gap_s=2.0))
    explicit_off = stack(
        [master, donor],
        _settings(position_from=WATCH_PATH, clock_align=False, max_gap_s=2.0),
    )

    assert [p.power for p in default.points()] == [
        p.power for p in explicit_off.points()
    ]
    # And with the skew left in, master-time 30 carries the donor's second-25
    # reading (250), not its true second-30 reading (300).
    assert _power_at(default, 30) == 250


@pytest.mark.unit
def test_clock_align_does_not_shift_the_master_timeline() -> None:
    master, donor = _aligned_master(), _skewed_donor()
    settings = _settings(position_from=WATCH_PATH, clock_align=True, max_gap_s=2.0)

    merged = stack([master, donor], settings)

    # The master's verbatim timeline, position, and elevation are untouched.
    assert [p.time for p in merged.points()] == [p.time for p in master.points()]
    assert [(p.lat, p.lon) for p in merged.points()] == [
        (p.lat, p.lon) for p in master.points()
    ]


@pytest.mark.unit
def test_clock_align_does_not_mutate_the_donor() -> None:
    master, donor = _aligned_master(), _skewed_donor()
    donor_times = [p.time for p in donor.points()]
    donor_power = [p.power for p in donor.points()]

    stack(
        [master, donor],
        _settings(position_from=WATCH_PATH, clock_align=True, max_gap_s=2.0),
    )

    assert [p.time for p in donor.points()] == donor_times
    assert [p.power for p in donor.points()] == donor_power
