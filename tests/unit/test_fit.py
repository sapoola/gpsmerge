"""Tests for FIT reading.

Fixtures are built with fit-tool (a test-only dependency) so the tests own real,
device-shaped binary FIT files rather than checked-in blobs. fit-tool expects
position in **degrees** (it encodes the semicircles itself) and timestamps as
**milliseconds** since the Unix epoch, so the helpers below speak those units.
"""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest
from fit_tool.fit_file_builder import FitFileBuilder
from fit_tool.profile.messages.event_message import EventMessage
from fit_tool.profile.messages.file_id_message import FileIdMessage
from fit_tool.profile.messages.record_message import RecordMessage
from fit_tool.profile.messages.sport_message import SportMessage
from fit_tool.profile.profile_type import (
    Event,
    EventType,
    FileType,
    GarminProduct,
    Manufacturer,
    Sport,
)

from gpsmerge.fit import read_fit
from gpsmerge.gpx import read_gpx, write_gpx

_BASE = datetime.datetime(2026, 6, 3, 12, 0, 0, tzinfo=datetime.timezone.utc)


def _ms(seconds: int) -> int:
    """Return milliseconds since the Unix epoch for `seconds` past the base time."""
    return round((_BASE + datetime.timedelta(seconds=seconds)).timestamp() * 1000)


def _file_id() -> FileIdMessage:
    """A Garmin Edge 530 file_id message with a serial and creation time."""
    msg = FileIdMessage()
    msg.type = FileType.ACTIVITY
    msg.manufacturer = Manufacturer.GARMIN.value
    msg.product = GarminProduct.EDGE_530.value
    msg.serial_number = 3_991_234_567
    msg.time_created = _ms(0)
    return msg


def _write_fit(tmp_path: Path, messages: list[object], name: str = "ride.fit") -> Path:
    """Build a FIT file from `messages` (in order) and return its path."""
    builder = FitFileBuilder(auto_define=True)
    for message in messages:
        builder.add(message)
    path = tmp_path / name
    builder.build().to_file(str(path))
    return path


@pytest.mark.unit
def test_read_fit_reads_all_records_with_their_channels(tmp_path: Path) -> None:
    full = RecordMessage()
    full.timestamp = _ms(0)
    full.position_lat = 52.1
    full.position_long = 4.1
    full.altitude = 10.0
    full.heart_rate = 140
    full.cadence = 85
    full.power = 220
    full.temperature = 21
    full.distance = 0.0
    full.speed = 5.0

    second = RecordMessage()
    second.timestamp = _ms(1)
    second.position_lat = 52.2
    second.position_long = 4.2
    second.heart_rate = 150

    path = _write_fit(tmp_path, [_file_id(), SportMessage(), full, second])
    activity = read_fit(path)

    points = list(activity.points())
    assert len(points) == 2

    first = points[0]
    # Semicircle quantisation costs us roughly 1e-5 deg of precision at most.
    assert first.lat == pytest.approx(52.1, abs=1e-5)
    assert first.lon == pytest.approx(4.1, abs=1e-5)
    assert first.ele == pytest.approx(10.0)
    assert first.hr == 140
    assert first.cad == 85
    assert first.power == 220
    assert first.temp == 21
    assert first.distance == pytest.approx(0.0)
    assert first.speed == pytest.approx(5.0)

    assert activity.source_format == "fit"
    assert activity.source_path == str(path)


@pytest.mark.unit
def test_read_fit_timestamps_are_timezone_aware_utc(tmp_path: Path) -> None:
    record = RecordMessage()
    record.timestamp = _ms(0)
    record.position_lat = 52.0
    record.position_long = 4.0

    path = _write_fit(tmp_path, [_file_id(), record])
    point = next(iter(read_fit(path).points()))

    assert point.time == _BASE
    assert point.time is not None
    assert point.time.tzinfo is not None
    assert point.time.utcoffset() == datetime.timedelta(0)


@pytest.mark.unit
def test_read_fit_brings_through_device_identity(tmp_path: Path) -> None:
    path = _write_fit(tmp_path, [_file_id(), _record_at(0)])
    device = read_fit(path).device

    assert device.manufacturer == "garmin"
    assert device.product == "edge_530"
    assert device.name == "garmin edge_530"
    assert device.serial_number == 3_991_234_567


@pytest.mark.unit
def test_read_fit_reads_sport_as_canonical_lowercase(tmp_path: Path) -> None:
    sport = SportMessage()
    sport.sport = Sport.CYCLING.value

    path = _write_fit(tmp_path, [_file_id(), sport, _record_at(0)])

    assert read_fit(path).sport == "cycling"


@pytest.mark.unit
def test_read_fit_keeps_absent_channels_as_none_not_zero(tmp_path: Path) -> None:
    # A record that carries only position and time: every sensor channel absent.
    bare = RecordMessage()
    bare.timestamp = _ms(0)
    bare.position_lat = 52.0
    bare.position_long = 4.0

    path = _write_fit(tmp_path, [_file_id(), bare])
    point = next(iter(read_fit(path).points()))

    assert point.hr is None
    assert point.cad is None
    assert point.power is None
    assert point.temp is None
    assert point.speed is None
    assert point.ele is None
    assert point.distance is None


@pytest.mark.unit
def test_read_fit_reads_enhanced_altitude_and_speed_when_only_those_are_set(
    tmp_path: Path,
) -> None:
    # fit-tool writes a malformed record when both the plain and enhanced
    # altitude/speed fields are set on one RecordMessage (it duplicates the
    # fields and crosses their values), so a genuine "both present" fixture is
    # not buildable here. Instead we prove the reader prefers/reads the enhanced
    # field by setting only the enhanced channels: if the reader did not read
    # them, ele and speed would come back None.
    record = RecordMessage()
    record.timestamp = _ms(0)
    record.position_lat = 52.0
    record.position_long = 4.0
    record.enhanced_altitude = 15.0
    record.enhanced_speed = 6.0

    path = _write_fit(tmp_path, [_file_id(), record])
    point = next(iter(read_fit(path).points()))

    assert point.ele == pytest.approx(15.0)
    assert point.speed == pytest.approx(6.0)


@pytest.mark.unit
def test_read_fit_falls_back_to_plain_altitude_and_speed(tmp_path: Path) -> None:
    # The other branch of the enhanced/plain preference: when no enhanced field
    # is present, the plain altitude and speed are used.
    record = RecordMessage()
    record.timestamp = _ms(0)
    record.position_lat = 52.0
    record.position_long = 4.0
    record.altitude = 10.0
    record.speed = 5.0

    path = _write_fit(tmp_path, [_file_id(), record])
    point = next(iter(read_fit(path).points()))

    assert point.ele == pytest.approx(10.0)
    assert point.speed == pytest.approx(5.0)


@pytest.mark.unit
def test_read_fit_splits_segments_at_a_timer_stop(tmp_path: Path) -> None:
    # Two records, a timer stop (the pause), then a third record after a resume.
    messages = [
        _file_id(),
        _timer(0, EventType.START),
        _record_at(0),
        _record_at(1),
        _timer(2, EventType.STOP),
        _timer(60, EventType.START),
        _record_at(60),
        _timer(61, EventType.STOP_ALL),
    ]
    path = _write_fit(tmp_path, messages)
    activity = read_fit(path)

    assert len(activity.segments) == 2
    assert len(activity.segments[0].points) == 2
    assert len(activity.segments[1].points) == 1


@pytest.mark.unit
def test_read_fit_to_gpx_round_trip_keeps_supported_channels(tmp_path: Path) -> None:
    # Read a FIT fixture, write it to GPX, read it back, and confirm the channels
    # GPX can carry survive the hop. (GPX has no per-point distance or speed in
    # the 1.1 flavour we emit, so those are not asserted here.)
    record = RecordMessage()
    record.timestamp = _ms(0)
    record.position_lat = 52.1
    record.position_long = 4.1
    # 12.4 is a whole multiple of FIT's 0.2 m altitude resolution, so it survives
    # the FIT -> GPX hop without quantisation noise.
    record.altitude = 12.4
    record.heart_rate = 145
    record.cadence = 90
    record.power = 200
    record.temperature = 19

    fit_path = _write_fit(tmp_path, [_file_id(), record])
    activity = read_fit(fit_path)

    gpx_path = tmp_path / "out.gpx"
    write_gpx(activity, gpx_path)
    result = read_gpx(gpx_path)

    point = next(iter(result.points()))
    assert point.lat == pytest.approx(52.1, abs=1e-5)
    assert point.lon == pytest.approx(4.1, abs=1e-5)
    assert point.ele == pytest.approx(12.4)
    assert point.hr == 145
    assert point.cad == 90
    assert point.power == 200
    assert point.temp == pytest.approx(19)


def _record_at(seconds: int) -> RecordMessage:
    """A minimal positioned record `seconds` past the base time."""
    record = RecordMessage()
    record.timestamp = _ms(seconds)
    record.position_lat = 52.0
    record.position_long = 4.0
    return record


def _timer(seconds: int, event_type: EventType) -> EventMessage:
    """A timer event (start/stop) `seconds` past the base time."""
    event = EventMessage()
    event.timestamp = _ms(seconds)
    event.event = Event.TIMER.value
    event.event_type = event_type.value
    return event
