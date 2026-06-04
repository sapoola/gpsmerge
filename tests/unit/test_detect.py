"""Tests for auto mode-detection (:func:`gpsmerge.merge.detect_mode`).

``detect_mode`` looks only at how much the recordings overlap in time, as a
fraction of the shorter one, and returns ``"stack"``, ``"append"``, or
``"ambiguous"``. These tests pin that classification at the boundaries.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from gpsmerge.merge import detect_mode
from gpsmerge.model import Activity, Segment, TrackPoint

_BASE = datetime(2026, 6, 4, 8, 0, 0, tzinfo=UTC)


def _span(start_offset: int, end_offset: int) -> Activity:
    """An activity with two timed points spanning the given offsets in seconds."""
    points = [
        TrackPoint(time=_BASE + timedelta(seconds=start_offset), lat=52.0, lon=4.0),
        TrackPoint(time=_BASE + timedelta(seconds=end_offset), lat=52.1, lon=4.1),
    ]
    return Activity(segments=[Segment(points=points)])


@pytest.mark.unit
def test_fully_overlapping_spans_detect_stack() -> None:
    # Identical windows: the recordings are the same ride, recorded twice.
    a = _span(0, 100)
    b = _span(0, 100)
    assert detect_mode([a, b]) == "stack"


@pytest.mark.unit
def test_disjoint_spans_detect_append() -> None:
    # The second starts well after the first ends: back-to-back recordings.
    a = _span(0, 100)
    b = _span(500, 600)
    assert detect_mode([a, b]) == "append"


@pytest.mark.unit
def test_half_overlapping_spans_are_ambiguous() -> None:
    # Each spans 100 s; they share 50 s -- a 0.5 ratio, between the thresholds.
    a = _span(0, 100)
    b = _span(50, 150)
    assert detect_mode([a, b]) == "ambiguous"


@pytest.mark.unit
def test_fewer_than_two_activities_is_ambiguous() -> None:
    assert detect_mode([_span(0, 100)]) == "ambiguous"
    assert detect_mode([]) == "ambiguous"


@pytest.mark.unit
def test_an_activity_with_no_timestamps_is_ambiguous() -> None:
    timed = _span(0, 100)
    untimed = Activity(
        segments=[Segment(points=[TrackPoint(time=None, lat=52.0, lon=4.0)])]
    )
    assert detect_mode([timed, untimed]) == "ambiguous"
