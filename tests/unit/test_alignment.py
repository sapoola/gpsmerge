"""Tests for clock-skew detection used by stacking.

:func:`detect_clock_offset` cross-correlates a channel two activities both
record and reports the seconds to add to the donor's timestamps so its shape
lines up with the master's. These tests build tiny in-code activities with a
known, varying shape and a known clock skew, and assert the offset comes back
with the right sign and magnitude -- and that it declines to shift (returns
``0.0``) when there is nothing to align on.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest

from gpsmerge.merge.alignment import (
    CONFIDENCE_THRESHOLD,
    best_shared_channel,
    detect_clock_offset,
)
from gpsmerge.model import Activity, Segment, TrackPoint

_BASE = datetime(2026, 6, 4, 8, 0, 0, tzinfo=UTC)


def _speed_at(second: int) -> float:
    """A clearly varying speed shape: a smooth wave so each instant is distinct."""
    return 5.0 + 3.0 * math.sin(second / 6.0)


def _activity(points: list[TrackPoint]) -> Activity:
    """Wrap points in a one-segment activity."""
    return Activity(segments=[Segment(points=points)])


def _speed_track(offset_s: float = 0.0, *, duration_s: int = 60) -> Activity:
    """Build a ~60 s track whose speed follows ``_speed_at``, stamped with a skew.

    ``offset_s`` is added to every timestamp, modelling a device whose clock is
    that many seconds fast (positive) or slow (negative) relative to true time.
    The *value* at a given true second is identical between tracks; only the
    stamped time differs, which is exactly the clock-skew situation.
    """
    points = [
        TrackPoint(
            time=_BASE + timedelta(seconds=second + offset_s),
            speed=_speed_at(second),
        )
        for second in range(duration_s + 1)
    ]
    return _activity(points)


@pytest.mark.unit
def test_no_shared_channel_returns_zero() -> None:
    # Master carries only heart rate; donor carries only power. They share no
    # channel, so there is nothing to correlate and no shift is proposed.
    master = _activity(
        [TrackPoint(time=_BASE + timedelta(seconds=s), hr=120 + s) for s in range(10)]
    )
    donor = _activity(
        [
            TrackPoint(time=_BASE + timedelta(seconds=s), power=200 + s)
            for s in range(10)
        ]
    )
    assert best_shared_channel(master, donor) is None
    assert detect_clock_offset(master, donor) == 0.0


@pytest.mark.unit
def test_a_flat_constant_channel_returns_zero() -> None:
    # Both record speed, but it never changes: a constant signal has no shape to
    # align, so the detector declines to shift.
    master = _activity(
        [TrackPoint(time=_BASE + timedelta(seconds=s), speed=5.0) for s in range(30)]
    )
    donor = _activity(
        [TrackPoint(time=_BASE + timedelta(seconds=s), speed=5.0) for s in range(30)]
    )
    assert best_shared_channel(master, donor) is None
    assert detect_clock_offset(master, donor) == 0.0


@pytest.mark.unit
def test_a_confident_match_recovers_the_correcting_offset() -> None:
    # The donor clock is 5 s fast: it stamps each reading 5 s later than the
    # master stamps the same reading. To line the donor up onto the master's
    # timeline we must subtract 5 s, so the correcting offset is about -5.
    master = _speed_track(offset_s=0.0)
    donor = _speed_track(offset_s=5.0)
    assert best_shared_channel(master, donor) == "speed"

    offset = detect_clock_offset(master, donor)
    assert offset == pytest.approx(-5.0, abs=1.0)


@pytest.mark.unit
def test_a_slow_donor_clock_gives_a_positive_offset() -> None:
    # The mirror case: a donor clock running 4 s slow stamps readings early, so
    # it must be moved later (a positive offset) to align.
    master = _speed_track(offset_s=0.0)
    donor = _speed_track(offset_s=-4.0)

    offset = detect_clock_offset(master, donor)
    assert offset == pytest.approx(4.0, abs=1.0)


@pytest.mark.unit
def test_already_aligned_tracks_need_no_shift() -> None:
    # Identical timing on both sides: the best lag is zero.
    master = _speed_track(offset_s=0.0)
    donor = _speed_track(offset_s=0.0)
    assert detect_clock_offset(master, donor) == 0.0


@pytest.mark.unit
def test_a_skew_beyond_the_search_window_is_not_shifted() -> None:
    # The true skew (20 s) lies outside a deliberately narrow search; no lag in
    # range lines the shapes up confidently, so no shift is proposed.
    master = _speed_track(offset_s=0.0, duration_s=120)
    donor = _speed_track(offset_s=20.0, duration_s=120)

    offset = detect_clock_offset(master, donor, max_skew_s=3.0)
    assert offset == 0.0


@pytest.mark.unit
def test_speed_is_preferred_over_other_shared_channels() -> None:
    # Both activities carry speed and heart rate that vary; speed wins by the
    # priority order.
    master = _activity(
        [
            TrackPoint(
                time=_BASE + timedelta(seconds=s), speed=_speed_at(s), hr=120 + s
            )
            for s in range(20)
        ]
    )
    donor = _activity(
        [
            TrackPoint(
                time=_BASE + timedelta(seconds=s), speed=_speed_at(s), hr=130 + s
            )
            for s in range(20)
        ]
    )
    assert best_shared_channel(master, donor) == "speed"


@pytest.mark.unit
def test_confidence_threshold_is_a_real_guard() -> None:
    # A guard a value rather than a hidden literal: it sits in (0, 1), so it can
    # actually reject a weak correlation without rejecting a strong one.
    assert 0.0 < CONFIDENCE_THRESHOLD < 1.0
