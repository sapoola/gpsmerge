"""Tests for the canonical data model."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from gpsmerge.model import Activity, Device, Segment, TrackPoint


@pytest.mark.unit
def test_trackpoint_channels_default_to_none() -> None:
    point = TrackPoint(time=datetime(2026, 1, 1, tzinfo=UTC))
    assert point.lat is None
    assert point.hr is None
    assert point.power is None


@pytest.mark.unit
def test_activity_points_iterates_across_segments_in_order() -> None:
    first = TrackPoint(time=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC))
    second = TrackPoint(time=datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC))
    third = TrackPoint(time=datetime(2026, 1, 1, 0, 5, 0, tzinfo=UTC))
    activity = Activity(
        segments=[Segment(points=[first, second]), Segment(points=[third])]
    )

    assert list(activity.points()) == [first, second, third]


@pytest.mark.unit
def test_activity_has_an_empty_device_by_default() -> None:
    activity = Activity()
    assert isinstance(activity.device, Device)
    assert activity.device.name is None


@pytest.mark.unit
def test_two_activities_do_not_share_a_device_instance() -> None:
    one = Activity()
    other = Activity()
    one.device.name = "Garmin Edge 530"
    assert other.device.name is None
