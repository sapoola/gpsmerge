"""Tests for great-circle geometry and semicircle conversions."""

from __future__ import annotations

import math
from datetime import UTC, datetime

import pytest

from gpsmerge.geo import (
    EARTH_RADIUS_M,
    cumulative_distance,
    degrees_to_semicircles,
    haversine,
    semicircles_to_degrees,
)
from gpsmerge.model import TrackPoint

# A degree of latitude along a meridian is EARTH_RADIUS_M * pi / 180 metres,
# independent of longitude. With the IUGG mean radius this is ~111.19 km.
_METRES_PER_DEGREE_LATITUDE = EARTH_RADIUS_M * math.pi / 180.0

# Amsterdam Centraal and the Rijksmuseum, ~2.36 km apart by great circle.
# The reference metres are computed independently with the spherical law of
# cosines, so this asserts a real-world distance rather than echoing haversine.
_AMSTERDAM_CENTRAAL = (52.3791, 4.9003)
_RIJKSMUSEUM = (52.3600, 4.8852)
_AMSTERDAM_DISTANCE_M = 2358.3


def _point_at(lat: float | None, lon: float | None, second: int) -> TrackPoint:
    return TrackPoint(
        time=datetime(2026, 1, 1, 0, 0, second, tzinfo=UTC), lat=lat, lon=lon
    )


@pytest.mark.unit
def test_haversine_of_one_degree_of_latitude_is_about_111_km() -> None:
    metres = haversine(0.0, 0.0, 1.0, 0.0)
    assert metres == pytest.approx(_METRES_PER_DEGREE_LATITUDE, rel=1e-9)
    assert metres == pytest.approx(111_194.9, abs=1.0)


@pytest.mark.unit
def test_haversine_matches_known_amsterdam_distance() -> None:
    metres = haversine(*_AMSTERDAM_CENTRAAL, *_RIJKSMUSEUM)
    assert metres == pytest.approx(_AMSTERDAM_DISTANCE_M, abs=1.0)


@pytest.mark.unit
def test_haversine_of_a_point_to_itself_is_zero() -> None:
    assert haversine(52.3791, 4.9003, 52.3791, 4.9003) == 0.0


@pytest.mark.unit
def test_haversine_is_symmetric() -> None:
    there = haversine(*_AMSTERDAM_CENTRAAL, *_RIJKSMUSEUM)
    back = haversine(*_RIJKSMUSEUM, *_AMSTERDAM_CENTRAAL)
    assert there == pytest.approx(back, rel=1e-12)


@pytest.mark.unit
def test_cumulative_distance_of_no_points_is_empty() -> None:
    assert cumulative_distance([]) == []


@pytest.mark.unit
def test_cumulative_distance_starts_at_zero() -> None:
    points = [_point_at(0.0, 0.0, 0)]
    assert cumulative_distance(points) == [0.0]


@pytest.mark.unit
def test_cumulative_distance_accumulates_each_step() -> None:
    points = [
        _point_at(0.0, 0.0, 0),
        _point_at(1.0, 0.0, 1),
        _point_at(2.0, 0.0, 2),
    ]
    totals = cumulative_distance(points)
    assert totals[0] == 0.0
    assert totals[1] == pytest.approx(_METRES_PER_DEGREE_LATITUDE, rel=1e-9)
    assert totals[2] == pytest.approx(2 * _METRES_PER_DEGREE_LATITUDE, rel=1e-9)


@pytest.mark.unit
def test_cumulative_distance_is_monotonic_non_decreasing() -> None:
    points = [
        _point_at(52.3791, 4.9003, 0),
        _point_at(52.3700, 4.8950, 1),
        _point_at(52.3600, 4.8852, 2),
        _point_at(52.3600, 4.8852, 3),
    ]
    totals = cumulative_distance(points)
    assert all(b >= a for a, b in zip(totals, totals[1:]))


@pytest.mark.unit
def test_cumulative_distance_carries_value_forward_for_position_less_point() -> None:
    points = [
        _point_at(0.0, 0.0, 0),
        _point_at(None, None, 1),  # no fix: must carry, not jump
        _point_at(1.0, 0.0, 2),
    ]
    totals = cumulative_distance(points)
    one_degree = _METRES_PER_DEGREE_LATITUDE
    assert totals[0] == 0.0
    assert totals[1] == 0.0
    # The leg skips the position-less point and measures 0,0 -> 1,0 directly.
    assert totals[2] == pytest.approx(one_degree, rel=1e-9)


@pytest.mark.unit
def test_cumulative_distance_carries_forward_when_only_one_coordinate_is_present() -> (
    None
):
    points = [
        _point_at(0.0, 0.0, 0),
        _point_at(1.0, None, 1),  # lat without lon is not a usable fix
        _point_at(1.0, 0.0, 2),
    ]
    totals = cumulative_distance(points)
    assert totals[1] == 0.0
    assert totals[2] == pytest.approx(_METRES_PER_DEGREE_LATITUDE, rel=1e-9)


@pytest.mark.unit
@pytest.mark.parametrize("degrees", [0.0, 90.0, -90.0])
def test_semicircle_degree_round_trip_is_exact(degrees: float) -> None:
    assert semicircles_to_degrees(degrees_to_semicircles(degrees)) == degrees


@pytest.mark.unit
def test_zero_degrees_is_zero_semicircles() -> None:
    assert degrees_to_semicircles(0.0) == 0
    assert semicircles_to_degrees(0) == 0.0


@pytest.mark.unit
def test_ninety_degrees_is_half_the_int32_range() -> None:
    assert degrees_to_semicircles(90.0) == 2**30
    assert semicircles_to_degrees(2**30) == 90.0


@pytest.mark.unit
def test_positive_180_clamps_to_int32_max_without_overflow() -> None:
    semicircles = degrees_to_semicircles(180.0)
    assert semicircles == 2**31 - 1


@pytest.mark.unit
def test_negative_180_clamps_to_int32_min() -> None:
    semicircles = degrees_to_semicircles(-180.0)
    assert semicircles == -(2**31)
