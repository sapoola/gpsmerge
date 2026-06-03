"""Tests for single-channel linear interpolation onto target times."""

from __future__ import annotations

import pytest

from gpsmerge.resample import lerp


@pytest.mark.unit
def test_empty_source_returns_none_for_every_target() -> None:
    result = lerp([0.0, 1.0, 2.0], [], max_gap_s=5.0)
    assert result == [None, None, None]


@pytest.mark.unit
def test_target_before_first_sample_is_none() -> None:
    src = [(10.0, 100.0), (20.0, 200.0)]
    assert lerp([9.999], src, max_gap_s=30.0) == [None]


@pytest.mark.unit
def test_target_after_last_sample_is_none() -> None:
    src = [(10.0, 100.0), (20.0, 200.0)]
    assert lerp([20.001], src, max_gap_s=30.0) == [None]


@pytest.mark.unit
def test_exact_midpoint_interpolates_halfway() -> None:
    src = [(0.0, 100.0), (10.0, 200.0)]
    assert lerp([5.0], src, max_gap_s=30.0) == [150.0]


@pytest.mark.unit
def test_exact_hit_on_a_sample_returns_that_value() -> None:
    src = [(0.0, 100.0), (10.0, 200.0), (20.0, 300.0)]
    assert lerp([0.0, 10.0, 20.0], src, max_gap_s=30.0) == [100.0, 200.0, 300.0]


@pytest.mark.unit
def test_gap_wider_than_max_gap_returns_none_inside_it() -> None:
    # The bracket spans 12 s but max_gap_s is 10 s: a real dropout, do not fill.
    src = [(0.0, 100.0), (12.0, 200.0)]
    assert lerp([6.0], src, max_gap_s=10.0) == [None]


@pytest.mark.unit
def test_gap_exactly_max_gap_still_interpolates() -> None:
    src = [(0.0, 100.0), (10.0, 200.0)]
    assert lerp([5.0], src, max_gap_s=10.0) == [150.0]


@pytest.mark.unit
def test_sparse_donor_onto_one_second_grid_interpolates_smoothly() -> None:
    # Samples every 4 s; resample onto a 1 s grid within the first interval.
    src = [(0.0, 0.0), (4.0, 40.0), (8.0, 80.0)]
    result = lerp([0.0, 1.0, 2.0, 3.0, 4.0], src, max_gap_s=10.0)
    assert result == [0.0, 10.0, 20.0, 30.0, 40.0]


@pytest.mark.unit
def test_returns_floats_without_rounding() -> None:
    # HR-like values: caller rounds to int, lerp must not.
    src = [(0.0, 100.0), (10.0, 105.0)]
    assert lerp([3.0], src, max_gap_s=30.0) == [101.5]


@pytest.mark.unit
def test_single_sample_only_matches_on_exact_time() -> None:
    src = [(5.0, 42.0)]
    assert lerp([4.0, 5.0, 6.0], src, max_gap_s=30.0) == [None, 42.0, None]
