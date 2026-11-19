"""Tests for FIT writing (the experimental :func:`gpsmerge.io.fit.write_fit`).

These exercise the write side by round-tripping through the read side: build an
:class:`~gpsmerge.model.Activity`, ``write_fit`` it, ``read_fit`` it back, and
assert the channels survive. ``read_fit`` is already trusted (it has its own
suite in ``test_fit.py``), so a round-trip is the honest end-to-end check that a
real device or Strava would also pass.

Fixtures use **whole-second** times on purpose: FIT record timestamps are
whole-second resolution, so a sub-second fixture would make the round-trip lie.
One test below documents that truncation explicitly rather than hiding it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from gpsmerge.errors import WriteError
from gpsmerge.io.fit import read_fit, write_fit
from gpsmerge.model import Activity, Device, Lap, Segment, TrackPoint

_BASE = datetime(2026, 6, 3, 12, 0, 0, tzinfo=UTC)


def _at(seconds: int) -> datetime:
    """Return a whole-second UTC time `seconds` past the base."""
    return _BASE + timedelta(seconds=seconds)


def _sample_activity() -> Activity:
    """An activity with two segments and a mix of populated and absent channels."""
    first = TrackPoint(
        time=_at(0),
        lat=52.1,
        lon=4.1,
        ele=10.0,
        distance=0.0,
        hr=140,
        cad=85,
        power=220,
        temp=21.0,
        speed=5.0,
    )
    # Every sensor channel absent: each must stay None, never become 0.
    second = TrackPoint(time=_at(1), lat=52.2, lon=4.2)
    # A separate segment, so a pause boundary is in play.
    third = TrackPoint(time=_at(60), lat=52.3, lon=4.3, hr=150)
    return Activity(
        sport="cycling",
        device=Device(name="Garmin Edge 530", manufacturer="garmin"),
        segments=[Segment(points=[first, second]), Segment(points=[third])],
        time_created=_at(0),
        source_format="gpx",  # came from elsewhere; we are writing FIT
    )


@pytest.mark.unit
def test_write_then_read_round_trips_every_supported_channel(tmp_path: Path) -> None:
    original = _sample_activity()
    path = tmp_path / "out.fit"

    write_fit(original, path)
    result = read_fit(path)

    assert result.source_format == "fit"
    assert result.source_path == str(path)

    original_points = list(original.points())
    result_points = list(result.points())
    assert len(result_points) == len(original_points)

    first_in, first_out = original_points[0], result_points[0]
    assert first_out.time == first_in.time
    # Semicircle quantisation costs roughly 1e-5 deg of precision at most.
    assert first_out.lat == pytest.approx(first_in.lat, abs=1e-5)
    assert first_out.lon == pytest.approx(first_in.lon, abs=1e-5)
    assert first_out.ele == pytest.approx(first_in.ele)
    assert first_out.distance == pytest.approx(first_in.distance)
    assert first_out.hr == first_in.hr
    assert first_out.cad == first_in.cad
    assert first_out.power == first_in.power
    assert first_out.temp == pytest.approx(first_in.temp)
    assert first_out.speed == pytest.approx(first_in.speed)


@pytest.mark.unit
def test_write_keeps_absent_channels_as_none_not_zero(tmp_path: Path) -> None:
    # The second point of the sample carries only position and time. After a
    # round-trip every sensor channel on it must read back as None, never 0 --
    # the model invariant we must not break on write.
    path = tmp_path / "out.fit"
    write_fit(_sample_activity(), path)
    bare = list(read_fit(path).points())[1]

    assert bare.ele is None
    assert bare.distance is None
    assert bare.hr is None
    assert bare.cad is None
    assert bare.power is None
    assert bare.temp is None
    assert bare.speed is None


@pytest.mark.unit
def test_write_emits_timer_stops_so_segments_survive(tmp_path: Path) -> None:
    # The sample has two segments split by a pause. read_fit rebuilds segments
    # from timer-stop events, so a faithful write must emit a stop between them.
    path = tmp_path / "out.fit"
    write_fit(_sample_activity(), path)
    result = read_fit(path)

    assert len(result.segments) == 2
    assert len(result.segments[0].points) == 2
    assert len(result.segments[1].points) == 1


@pytest.mark.unit
def test_write_round_trips_sport(tmp_path: Path) -> None:
    path = tmp_path / "out.fit"
    write_fit(_sample_activity(), path)

    assert read_fit(path).sport == "cycling"


@pytest.mark.unit
def test_write_omits_sport_message_for_an_unknown_sport(tmp_path: Path) -> None:
    # A sport we have no FIT enum for is dropped rather than guessed, so it reads
    # back as None instead of a wrong sport.
    activity = _sample_activity()
    activity.sport = "kayaking"  # not in the canonical FIT-mapped set
    path = tmp_path / "out.fit"
    write_fit(activity, path)

    assert read_fit(path).sport is None


@pytest.mark.unit
def test_write_round_trips_laps_with_spans_and_triggers(tmp_path: Path) -> None:
    activity = _sample_activity()
    activity.laps = [
        Lap(start_time=_at(0), end_time=_at(30), trigger="manual"),
        Lap(start_time=_at(30), end_time=_at(60), trigger="distance"),
    ]
    path = tmp_path / "out.fit"
    write_fit(activity, path)
    laps = read_fit(path).laps

    assert len(laps) == 2
    assert laps[0].start_time == _at(0)
    assert laps[0].end_time == _at(30)
    assert laps[0].trigger == "manual"
    assert laps[1].start_time == _at(30)
    assert laps[1].trigger == "distance"


@pytest.mark.unit
def test_write_attributes_device_serial_best_effort(tmp_path: Path) -> None:
    # Device attribution on FIT write is best-effort: the manufacturer maps back
    # by name and the serial number survives, but we do not claim a perfect
    # round-trip of the whole Device, so name equality is not asserted.
    activity = _sample_activity()
    activity.device = Device(
        name="Garmin Edge 530", manufacturer="garmin", serial_number=987654321
    )
    path = tmp_path / "out.fit"
    write_fit(activity, path)
    device = read_fit(path).device

    assert device.manufacturer == "garmin"
    assert device.serial_number == 987654321


@pytest.mark.unit
def test_write_drops_an_unmappable_manufacturer(tmp_path: Path) -> None:
    # A manufacturer string with no matching FIT enum is omitted (not forced to a
    # wrong code), so it reads back as None rather than mis-attributed.
    activity = _sample_activity()
    activity.device = Device(name="Acme 9000", manufacturer="acme_widgets")
    path = tmp_path / "out.fit"
    write_fit(activity, path)

    assert read_fit(path).device.manufacturer is None


@pytest.mark.unit
def test_write_truncates_subsecond_times_documenting_the_loss(tmp_path: Path) -> None:
    # FIT record timestamps are whole-second resolution, so the fractional part
    # of a sub-second time is truncated on write. This is a documented FIT-write
    # loss (like GPX speed and TCX temperature), proven here rather than pretended
    # away: the point reads back on the whole second it was truncated to.
    subsecond = _BASE + timedelta(seconds=0, milliseconds=500)
    activity = Activity(
        segments=[Segment(points=[TrackPoint(time=subsecond, lat=52.0, lon=4.0)])],
    )
    path = tmp_path / "out.fit"
    write_fit(activity, path)
    point = next(iter(read_fit(path).points()))

    assert point.time == _BASE  # the .500 fraction is gone


@pytest.mark.unit
def test_write_rounds_temperature_to_whole_degrees(tmp_path: Path) -> None:
    # FIT stores temperature as a signed byte at 1 degC resolution, so the
    # fractional degree is dropped on write. A documented loss, pinned here so the
    # round-trip suite does not quietly imply temperature is lossless.
    point = TrackPoint(time=_at(0), lat=52.0, lon=4.0, temp=21.5)
    activity = Activity(segments=[Segment(points=[point])])
    path = tmp_path / "out.fit"
    write_fit(activity, path)

    assert next(iter(read_fit(path).points())).temp == 21.0  # the .5 degree is gone


@pytest.mark.unit
def test_write_reads_back_a_sentinel_valued_channel_as_absent(tmp_path: Path) -> None:
    # FIT marks an absent channel with the type's maximum, so a real reading that
    # lands on that sentinel (heart rate 255) reads back as None. The value is
    # physically implausible, so we write it as-is rather than nudge real data --
    # a documented boundary, pinned here so the behaviour stays explicit.
    point = TrackPoint(time=_at(0), lat=52.0, lon=4.0, hr=255)
    activity = Activity(segments=[Segment(points=[point])])
    path = tmp_path / "out.fit"
    write_fit(activity, path)

    assert next(iter(read_fit(path).points())).hr is None


@pytest.mark.unit
def test_write_raises_a_clear_error_for_an_unrepresentable_channel(
    tmp_path: Path,
) -> None:
    # A value beyond what FIT can store (power above 65535 W) makes fit-tool raise
    # an opaque error on assignment. The same activity exports fine to GPX/TCX, so
    # the FIT writer must fail with an actionable gpsmerge error naming the point,
    # never a raw traceback.
    point = TrackPoint(time=_at(0), lat=52.0, lon=4.0, power=65536)
    activity = Activity(segments=[Segment(points=[point])])

    with pytest.raises(WriteError, match=r"to FIT") as excinfo:
        write_fit(activity, tmp_path / "out.fit")
    assert "65536" in str(excinfo.value)


@pytest.mark.unit
def test_write_fit_raises_a_helpful_error_when_fit_tool_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # fit-tool is an optional dependency, imported lazily inside write_fit. When
    # it is absent the user must get an actionable install hint, not an opaque
    # ImportError. Simulate the missing import by blocking the package.
    import builtins

    real_import = builtins.__import__

    def _blocked(name: str, *args: object, **kwargs: object) -> object:
        if name.startswith("fit_tool"):
            raise ImportError("No module named 'fit_tool'")
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", _blocked)

    with pytest.raises(RuntimeError, match=r"gpsmerge\[fit\]"):
        write_fit(_sample_activity(), tmp_path / "out.fit")
