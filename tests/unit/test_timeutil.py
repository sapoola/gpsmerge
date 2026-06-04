"""Tests for the timestamp helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from gpsmerge.timeutil import (
    FIT_EPOCH_OFFSET_S,
    fit_timestamp_to_utc,
    parse_duration,
    parse_time,
    parse_timezone,
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


# --- parse_timezone ------------------------------------------------------

# An arbitrary instant used to read each parsed zone's actual offset.
_SAMPLE = datetime(2026, 6, 4, 12, 0, 0, tzinfo=UTC)


@pytest.mark.unit
def test_parse_timezone_colon_offset_gives_two_hours_east() -> None:
    zone = parse_timezone("+02:00")
    assert _SAMPLE.astimezone(zone).utcoffset() == timedelta(hours=2)


@pytest.mark.unit
def test_parse_timezone_bare_hour_offset_gives_two_hours_east() -> None:
    zone = parse_timezone("+2")
    assert _SAMPLE.astimezone(zone).utcoffset() == timedelta(hours=2)


@pytest.mark.unit
def test_parse_timezone_negative_half_hour_offset_is_minus_five_thirty() -> None:
    zone = parse_timezone("-05:30")
    assert _SAMPLE.astimezone(zone).utcoffset() == timedelta(hours=-5, minutes=-30)


@pytest.mark.unit
def test_parse_timezone_utc_alias_is_zero_offset() -> None:
    assert _SAMPLE.astimezone(parse_timezone("UTC")).utcoffset() == timedelta(0)


@pytest.mark.unit
def test_parse_timezone_z_and_zero_aliases_are_zero_offset() -> None:
    assert _SAMPLE.astimezone(parse_timezone("Z")).utcoffset() == timedelta(0)
    assert _SAMPLE.astimezone(parse_timezone("0")).utcoffset() == timedelta(0)


@pytest.mark.unit
def test_parse_timezone_amsterdam_is_summer_time_in_june() -> None:
    # CEST: Europe/Amsterdam is one hour ahead of CET in June.
    zone = parse_timezone("Europe/Amsterdam")
    june = datetime(2026, 6, 4, 12, 0, 0, tzinfo=UTC).astimezone(zone)
    assert june.utcoffset() == timedelta(hours=2)


@pytest.mark.unit
def test_parse_timezone_amsterdam_is_standard_time_in_january() -> None:
    # CET: the same IANA name resolves to +01:00 in winter -- DST is automatic.
    zone = parse_timezone("Europe/Amsterdam")
    january = datetime(2026, 1, 4, 12, 0, 0, tzinfo=UTC).astimezone(zone)
    assert january.utcoffset() == timedelta(hours=1)


@pytest.mark.unit
def test_parse_timezone_raises_value_error_on_garbage() -> None:
    with pytest.raises(ValueError):
        parse_timezone("Mars/Olympus_Mons")


@pytest.mark.unit
def test_parse_timezone_raises_value_error_on_empty_spec() -> None:
    with pytest.raises(ValueError):
        parse_timezone("   ")


# --- parse_duration ------------------------------------------------------


@pytest.mark.unit
def test_parse_duration_hours_unit_with_explicit_plus() -> None:
    assert parse_duration("+1h") == timedelta(hours=1)


@pytest.mark.unit
def test_parse_duration_minutes_unit_negates_with_leading_minus() -> None:
    assert parse_duration("-30m") == timedelta(minutes=-30)


@pytest.mark.unit
def test_parse_duration_seconds_unit_without_a_sign() -> None:
    assert parse_duration("90s") == timedelta(seconds=90)


@pytest.mark.unit
def test_parse_duration_accepts_a_decimal_value() -> None:
    assert parse_duration("45.5s") == timedelta(seconds=45.5)


@pytest.mark.unit
def test_parse_duration_clock_form_hours_minutes_seconds() -> None:
    assert parse_duration("+1:30:00") == timedelta(hours=1, minutes=30)


@pytest.mark.unit
def test_parse_duration_clock_form_minutes_seconds_negated() -> None:
    assert parse_duration("-0:02:30") == timedelta(minutes=-2, seconds=-30)


@pytest.mark.unit
def test_parse_duration_clock_form_bare_minutes_seconds() -> None:
    assert parse_duration("2:30") == timedelta(minutes=2, seconds=30)


@pytest.mark.unit
def test_parse_duration_raises_value_error_on_garbage() -> None:
    with pytest.raises(ValueError):
        parse_duration("soon")


@pytest.mark.unit
def test_parse_duration_raises_value_error_on_a_bare_number() -> None:
    # A unit is required for the single-value form.
    with pytest.raises(ValueError):
        parse_duration("90")


@pytest.mark.unit
def test_parse_duration_raises_value_error_on_empty_spec() -> None:
    with pytest.raises(ValueError):
        parse_duration("   ")
