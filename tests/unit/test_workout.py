"""Tests for the BETA effort-interval workout detector.

The detector needs numpy (the optional ``workout`` extra). Every test here is
marked so the whole module skips cleanly when numpy is not installed, rather
than erroring at import time.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from gpsmerge.errors import WorkoutError
from gpsmerge.model import Activity, Segment, TrackPoint

np = pytest.importorskip("numpy", reason="workout detection needs the numpy extra")

from gpsmerge.workout import detect_workout  # noqa: E402

pytestmark = pytest.mark.unit

_BASE = datetime(2026, 6, 4, 8, 0, 0, tzinfo=UTC)


def _activity_from_power(power: list[float | None]) -> Activity:
    """Build a one-segment activity with one 1 Hz power sample per value."""
    points = [
        TrackPoint(
            time=_BASE + timedelta(seconds=i),
            power=None if value is None else int(round(value)),
        )
        for i, value in enumerate(power)
    ]
    return Activity(segments=[Segment(points=points)])


def _activity_from_speed(speed: list[float | None]) -> Activity:
    """Build a one-segment activity with one 1 Hz speed sample per value."""
    points = [
        TrackPoint(time=_BASE + timedelta(seconds=i), speed=value)
        for i, value in enumerate(speed)
    ]
    return Activity(segments=[Segment(points=points)])


def _steps(spec: list[tuple[float, float]], *, noise: float = 0.0) -> list[float]:
    """Expand ``[(seconds, level), ...]`` into a flat per-second series.

    With ``noise`` set, gaussian noise of that standard deviation is added to
    each sample (seeded for reproducibility), so tests exercise the smoothing
    and significance logic against a realistic jittery trace.
    """
    rng = np.random.default_rng(1234)
    series: list[float] = []
    for seconds, level in spec:
        block = [float(level)] * int(seconds)
        if noise:
            block = [v + float(rng.normal(0.0, noise)) for v in block]
        series.extend(block)
    return series


# --- power: the headline structured-workout case ----------------------------


def test_power_recovers_four_intervals_with_right_durations_and_averages() -> None:
    # 300s @ 300, 300s @ 180, 30s @ 500, 20s @ 200 -- the brief from the spec.
    power = _steps(
        [(300, 300), (300, 180), (30, 500), (20, 200)],
        noise=8.0,
    )
    workout = detect_workout(_activity_from_power(power))

    assert workout.channel == "power"
    # Allow a one-interval wobble either way; the heuristic should land on ~4.
    assert 4 <= len(workout.intervals) <= 5
    # The first two long blocks must be recovered close to their true levels and
    # durations.
    first, second = workout.intervals[0], workout.intervals[1]
    assert first.average == pytest.approx(300, abs=20)
    assert second.average == pytest.approx(180, abs=20)
    assert first.duration_seconds == pytest.approx(300, abs=20)
    assert second.duration_seconds == pytest.approx(300, abs=20)
    # The 500 W sprint must show up as a high-power short interval somewhere.
    assert any(iv.average > 420 for iv in workout.intervals)


def test_power_summary_is_in_watts_rounded_to_five() -> None:
    power = _steps([(120, 252), (120, 178)], noise=4.0)
    workout = detect_workout(_activity_from_power(power))

    assert "W" in workout.summary
    # Every wattage in the summary is a multiple of 5.
    watts = [
        int(token.split()[0])
        for token in workout.summary.split(", ")
        for piece in [token]
        if "W" in piece
        for token in [piece.split("@")[1]]
    ]
    for w in watts:
        assert w % 5 == 0


# --- the crux: significance scaled to the ride's OWN average -----------------


def test_low_average_ride_detects_the_small_step() -> None:
    # Average ~115 W. A 100->130 W step is +27% of average, well over K_AVG=0.15,
    # so it is a real interval -> TWO blocks.
    power = _steps([(300, 100), (300, 130)], noise=3.0)
    workout = detect_workout(_activity_from_power(power))

    assert len(workout.intervals) == 2
    assert workout.intervals[0].average == pytest.approx(100, abs=12)
    assert workout.intervals[1].average == pytest.approx(130, abs=12)


def test_high_average_ride_absorbs_the_brief_low_dip_into_one_block() -> None:
    # A mostly-350 W ride with a BRIEF 100->130 W dip. Two things conspire to keep
    # it one block: the dip is short (below min_seconds, so it is not a real rep),
    # and delta_min scales to the ~350 W average (0.15 * 350 ~= 52 W), so the
    # within-block jitter never fragments the 350 W effort. -> ONE block.
    power = _steps(
        [(298, 350), (2, 100), (2, 130), (298, 350)],
        noise=4.0,
    )
    workout = detect_workout(_activity_from_power(power))

    assert len(workout.intervals) == 1
    assert workout.intervals[0].average == pytest.approx(350, abs=20)


# --- speed / running: pace summary ------------------------------------------


def test_speed_detects_intervals_and_summarises_in_pace_per_km() -> None:
    # 3.0 m/s (~5:33/km) vs 4.5 m/s (~3:42/km): a clear run interval series.
    speed = _steps([(180, 3.0), (60, 4.5), (180, 3.0), (60, 4.5)], noise=0.05)
    workout = detect_workout(_activity_from_speed(speed))

    assert workout.channel == "speed"
    assert len(workout.intervals) >= 3
    # The summary is a pace (mm:ss/km), not a speed in m/s.
    assert "/km" in workout.summary
    assert "m/s" not in workout.summary
    # 3.0 m/s is 1000/3 = 333s = 5:33/km; the slow blocks should render near it.
    assert "5:3" in workout.summary or "5:33" in workout.summary


def test_power_wins_over_speed_when_both_present() -> None:
    # A ride that carries both a varying power and a varying speed channel must
    # be detected from power (the ride channel), not speed.
    power = _steps([(120, 300), (120, 180)], noise=4.0)
    activity = _activity_from_power(power)
    # Add a varying speed channel onto the same points.
    speeds = _steps([(120, 8.0), (120, 6.0)], noise=0.1)
    for point, speed in zip(activity.segments[0].points, speeds):
        point.speed = speed

    workout = detect_workout(activity)
    assert workout.channel == "power"


# --- short-rep survival vs the min_seconds blip merge ------------------------


def test_a_real_short_sprint_survives_between_two_blocks() -> None:
    # A 20 s @ 500 W sprint sandwiched between two steady ~200 W blocks: at the
    # default 10 s minimum it is longer than the floor, so it survives as its own
    # interval.
    power = _steps([(120, 200), (20, 500), (120, 200)], noise=5.0)
    workout = detect_workout(_activity_from_power(power), min_seconds=10.0)

    assert any(
        iv.average > 420 and iv.duration_seconds == pytest.approx(20, abs=8)
        for iv in workout.intervals
    )


def test_min_seconds_merges_a_four_second_blip() -> None:
    # A 4 s blip is below the 10 s minimum interval, so it is absorbed into its
    # neighbours -> one block, regardless of how distinct its level is.
    power = _steps([(150, 250), (4, 400), (150, 250)], noise=4.0)
    workout = detect_workout(_activity_from_power(power), min_seconds=10.0)

    assert len(workout.intervals) == 1
    assert workout.intervals[0].average == pytest.approx(250, abs=20)


def test_raising_min_seconds_coarsens_a_busy_interval_ride() -> None:
    # Eight 30 s reps alternating 350/200 W. At the 10 s default every 30 s rep
    # survives (it is longer than the floor), so the overlay is busy; raising the
    # floor to 60 s folds the 30 s reps away, yielding strictly fewer intervals.
    # This is the knob actually biting -- the lever for a coarser overlay.
    power = _steps([(30, 350), (30, 200)] * 4, noise=5.0)
    activity = _activity_from_power(power)

    fine = detect_workout(activity, min_seconds=10.0)
    coarse = detect_workout(activity, min_seconds=60.0)

    assert len(fine.intervals) >= 4
    assert len(coarse.intervals) < len(fine.intervals)


def test_a_flat_zero_power_channel_with_glitches_does_not_beat_a_real_run() -> None:
    # A dropped power meter reads a flat 0 W but emits a few stray spike frames,
    # while the speed channel carries a genuine run. A raw max-min spread would
    # pick the glitchy "power" and report the run as 0 W; the robust percentile
    # spread keeps power classified as flat, so speed (the real signal) is chosen.
    speed = _steps([(120, 3.0), (60, 4.5), (120, 3.0), (60, 4.5)], noise=0.05)
    activity = _activity_from_speed(speed)
    flat_power = [0.0] * len(speed)
    for spike_at in (10, 50, 130, 200, 300):  # lone glitch frames in a dead trace
        flat_power[spike_at] = 600.0
    for point, watts in zip(activity.segments[0].points, flat_power):
        point.power = int(watts)

    workout = detect_workout(activity)

    assert workout.channel == "speed"
    assert "/km" in workout.summary


# --- no-data, constant, and ramp behaviour ----------------------------------


def test_no_power_and_no_speed_raises_a_clear_error() -> None:
    points = [TrackPoint(time=_BASE + timedelta(seconds=i), hr=140) for i in range(120)]
    activity = Activity(segments=[Segment(points=points)])

    with pytest.raises(WorkoutError, match="no usable power or speed"):
        detect_workout(activity)


def test_a_dead_zero_power_channel_is_not_usable() -> None:
    # A disconnected power meter pinned to 0 W has no real effort (95th percentile
    # 0 < floor), so power is not usable -- and with no speed either, the clear
    # error is raised rather than a fabricated "0 W" workout.
    activity = _activity_from_power([0.0] * 120)

    with pytest.raises(WorkoutError, match="no usable power or speed"):
        detect_workout(activity)


def test_constant_effort_yields_a_single_lap() -> None:
    # A steady block is one sustained effort -> exactly one interval and one lap.
    # A constant trace is usable on its own (selection is on effort magnitude, not
    # variation), so no lead-in ramp is needed to make it "vary".
    power = _steps([(300, 300)], noise=3.0)
    workout = detect_workout(_activity_from_power(power))

    assert len(workout.intervals) == 1
    assert workout.laps  # laps were produced
    assert workout.intervals[0].average == pytest.approx(300, abs=20)


def test_a_smooth_ramp_does_not_explode_into_dozens_of_laps() -> None:
    # A continuous 100 -> 400 W ramp over 10 minutes has no real steps. The
    # detector is documented to handle ramps poorly, but it must NOT fragment one
    # into dozens of laps -- a handful of coarse steps is acceptable.
    ramp = [100.0 + (300.0 * i / 600.0) for i in range(600)]
    workout = detect_workout(_activity_from_power(ramp))

    assert len(workout.intervals) <= 12


# --- laps overlay and trigger -----------------------------------------------


def test_laps_carry_the_channel_trigger_and_cover_the_activity() -> None:
    power = _steps([(120, 300), (120, 180)], noise=4.0)
    workout = detect_workout(_activity_from_power(power))

    assert workout.laps
    # Completed laps are tagged with the channel; only a possible run-to-end lap
    # is "session_end" (here the boundaries land on the end, so none is added).
    assert all(lap.trigger in ("power", "session_end") for lap in workout.laps)
    # The laps are contiguous and cover from the first interval start onward.
    assert workout.laps[0].start_time == workout.intervals[0].start_time
