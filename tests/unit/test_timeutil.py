"""Tests for the timestamp helpers."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from gpsmerge.timeutil import (
    FIT_EPOCH_OFFSET_S,
    fit_timestamp_to_utc,
    parse_time,
    to_epoch_seconds,
    utc_to_fit_timestamp,
)


@pytest.mark.unit
def test_parse_time_with_trailing_z_yields_utc() -> None:
    result = parse_time("2026-06-03T12:00:00Z")
    assert result == datetime(2026, 6, 3, 12, 0, 0, tzinfo=UTC)
    assert result.tzinfo == UTC


@pytest.mark.unit
def test_parse_time_with_numeric_offset_converts_to_the_same_utc_instant() -> None:
    result = parse_time("2026-06-03T14:00:00+02:00")
    assert result == datetime(2026, 6, 3, 12, 0, 0, tzinfo=UTC)
    assert result.tzinfo == UTC


@pytest.mark.unit
def test_parse_time_with_no_zone_is_assumed_to_be_utc() -> None:
    result = parse_time("2026-06-03T12:00:00")
    assert result == datetime(2026, 6, 3, 12, 0, 0, tzinfo=UTC)
    assert result.tzinfo == UTC


@pytest.mark.unit
def test_parse_time_forms_all_describe_the_same_instant() -> None:
    with_z = parse_time("2026-06-03T12:00:00Z")
    with_offset = parse_time("2026-06-03T14:00:00+02:00")
    naive = parse_time("2026-06-03T12:00:00")
    assert with_z == with_offset == naive


@pytest.mark.unit
def test_parse_time_raises_value_error_on_garbage() -> None:
    with pytest.raises(ValueError):
        parse_time("not-a-timestamp")


@pytest.mark.unit
def test_to_epoch_seconds_preserves_a_fractional_half_second() -> None:
    dt = datetime(2026, 6, 3, 12, 0, 0, 500_000, tzinfo=UTC)
    seconds = to_epoch_seconds(dt)
    assert seconds == pytest.approx(int(seconds) + 0.5)


@pytest.mark.unit
def test_to_epoch_seconds_assumes_naive_input_is_utc() -> None:
    naive = datetime(2026, 6, 3, 12, 0, 0)
    aware = datetime(2026, 6, 3, 12, 0, 0, tzinfo=UTC)
    assert to_epoch_seconds(naive) == to_epoch_seconds(aware)


@pytest.mark.unit
def test_fit_timestamp_to_utc_zero_is_the_fit_epoch() -> None:
    assert fit_timestamp_to_utc(0) == datetime(1989, 12, 31, 0, 0, 0, tzinfo=UTC)


@pytest.mark.unit
def test_fit_epoch_offset_matches_the_fit_epoch_in_unix_seconds() -> None:
    assert FIT_EPOCH_OFFSET_S == int(datetime(1989, 12, 31, tzinfo=UTC).timestamp())


@pytest.mark.unit
def test_utc_to_fit_timestamp_round_trips_a_sample_value() -> None:
    sample = 1_000_000_000
    assert utc_to_fit_timestamp(fit_timestamp_to_utc(sample)) == sample
