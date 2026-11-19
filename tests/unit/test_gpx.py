"""Tests for GPX reading and writing."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from gpsmerge.io.gpx import read_gpx, write_gpx
from gpsmerge.model import Activity, Device, Segment, TrackPoint


def _sample_activity() -> Activity:
    """Build an activity with two segments and a mix of populated/absent channels."""
    first = TrackPoint(
        time=datetime(2026, 6, 3, 12, 0, 0, tzinfo=UTC),
        lat=52.1,
        lon=4.1,
        ele=10.0,
        hr=140,
        cad=85,
        power=220,
        temp=21.5,
    )
    # Every fitness channel absent: must stay None, never become 0.
    second = TrackPoint(
        time=datetime(2026, 6, 3, 12, 0, 1, tzinfo=UTC),
        lat=52.2,
        lon=4.2,
        ele=11.0,
    )
    third = TrackPoint(
        time=datetime(2026, 6, 3, 12, 5, 0, tzinfo=UTC),
        lat=52.3,
        lon=4.3,
        ele=12.0,
        hr=150,
    )
    return Activity(
        sport="cycling",
        name="Morning Ride",
        device=Device(name="Garmin Edge 530"),
        segments=[Segment(points=[first, second]), Segment(points=[third])],
        time_created=datetime(2026, 6, 3, 11, 0, 0, tzinfo=UTC),
        source_format="gpx",
    )


@pytest.mark.unit
def test_write_then_read_round_trips_every_field(tmp_path: Path) -> None:
    original = _sample_activity()
    path = tmp_path / "out.gpx"

    write_gpx(original, path)
    result = read_gpx(path)

    assert result.sport == original.sport
    assert result.name == original.name
    assert result.time_created == original.time_created
    assert result.device.name == original.device.name
    assert result.source_format == "gpx"
    assert result.source_path == str(path)

    original_points = list(original.points())
    result_points = list(result.points())
    assert len(result_points) == len(original_points)

    for got, want in zip(result_points, original_points):
        assert got.time == want.time
        assert got.lat == pytest.approx(want.lat)
        assert got.lon == pytest.approx(want.lon)
        assert got.ele == pytest.approx(want.ele)
        assert got.hr == want.hr
        assert got.cad == want.cad
        assert got.power == want.power
        assert got.temp == want.temp


@pytest.mark.unit
def test_round_trip_keeps_absent_channels_as_none_not_zero(tmp_path: Path) -> None:
    original = _sample_activity()
    path = tmp_path / "out.gpx"

    write_gpx(original, path)
    result = read_gpx(path)

    # The second point of the first segment had no fitness channels.
    bare = result.segments[0].points[1]
    assert bare.hr is None
    assert bare.cad is None
    assert bare.power is None
    assert bare.temp is None


@pytest.mark.unit
def test_gpx_write_drops_speed_and_distance(tmp_path: Path) -> None:
    # GPX 1.1 has no trackpoint <speed> or <distance> element, so these channels
    # cannot survive a write. read_gpx returns them as None -- a documented loss.
    activity = Activity(
        segments=[
            Segment(
                points=[
                    TrackPoint(
                        time=datetime(2026, 6, 3, 12, 0, 0, tzinfo=UTC),
                        lat=52.0,
                        lon=4.0,
                        speed=5.5,
                        distance=123.4,
                    )
                ]
            )
        ],
    )
    path = tmp_path / "out.gpx"

    write_gpx(activity, path)
    point = list(read_gpx(path).points())[0]

    assert point.speed is None
    assert point.distance is None


@pytest.mark.unit
def test_read_picks_up_speed_from_a_gpx_1_0_file(tmp_path: Path) -> None:
    # Older GPX 1.0 files store <speed> on a trackpoint; we still read it.
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<gpx creator="Old Device" version="1.0"
     xmlns="http://www.topografix.com/GPX/1/0">
 <trk>
  <trkseg>
   <trkpt lat="52.0" lon="4.0">
    <time>2026-06-03T08:00:00Z</time>
    <speed>4.2</speed>
   </trkpt>
  </trkseg>
 </trk>
</gpx>
"""
    path = tmp_path / "v10.gpx"
    path.write_text(xml, encoding="utf-8")

    point = list(read_gpx(path).points())[0]
    assert point.speed == pytest.approx(4.2)


@pytest.mark.unit
def test_round_trip_preserves_segment_boundaries(tmp_path: Path) -> None:
    original = _sample_activity()
    path = tmp_path / "out.gpx"

    write_gpx(original, path)
    result = read_gpx(path)

    assert len(result.segments) == 2
    assert len(result.segments[0].points) == 2
    assert len(result.segments[1].points) == 1


@pytest.mark.unit
def test_write_falls_back_to_gpsmerge_creator_when_device_name_is_none(
    tmp_path: Path,
) -> None:
    activity = Activity(
        device=Device(name=None),
        segments=[
            Segment(
                points=[
                    TrackPoint(
                        time=datetime(2026, 6, 3, 12, 0, 0, tzinfo=UTC),
                        lat=52.0,
                        lon=4.0,
                    )
                ]
            )
        ],
    )
    path = tmp_path / "out.gpx"

    write_gpx(activity, path)
    result = read_gpx(path)

    assert result.device.name == "gpsmerge"


@pytest.mark.unit
def test_read_parses_a_hand_written_gpx_with_a_garmin_extension(
    tmp_path: Path,
) -> None:
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<gpx creator="Wahoo ELEMNT" version="1.1"
     xmlns="http://www.topografix.com/GPX/1/1"
     xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1"
     xmlns:gpxpx="http://www.garmin.com/xmlschemas/PowerExtension/v1">
 <trk>
  <type>Running</type>
  <trkseg>
   <trkpt lat="52.0" lon="4.0">
    <ele>5.0</ele>
    <time>2026-06-03T08:00:00Z</time>
    <extensions>
     <gpxtpx:TrackPointExtension>
      <gpxtpx:hr>120</gpxtpx:hr>
      <gpxtpx:cad>80</gpxtpx:cad>
      <gpxtpx:atemp>18.0</gpxtpx:atemp>
     </gpxtpx:TrackPointExtension>
     <gpxpx:PowerInWatts>180</gpxpx:PowerInWatts>
    </extensions>
   </trkpt>
   <trkpt lat="52.001" lon="4.001">
    <time>2026-06-03T08:00:01Z</time>
   </trkpt>
  </trkseg>
 </trk>
</gpx>
"""
    path = tmp_path / "hand.gpx"
    path.write_text(xml, encoding="utf-8")

    activity = read_gpx(path)

    assert activity.device.name == "Wahoo ELEMNT"
    assert activity.sport == "running"  # lowercased from <type>Running</type>
    points = list(activity.points())
    assert points[0].time == datetime(2026, 6, 3, 8, 0, 0, tzinfo=UTC)
    assert points[0].hr == 120
    assert points[0].cad == 80
    assert points[0].temp == pytest.approx(18.0)
    assert points[0].power == 180
    # The second point carries no extensions: channels are None, not 0.
    assert points[1].hr is None
    assert points[1].power is None


@pytest.mark.unit
def test_read_handles_power_from_plain_power_element(tmp_path: Path) -> None:
    # Some writers emit a bare <power> child rather than gpxpx:PowerInWatts.
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<gpx creator="Some Device" version="1.1"
     xmlns="http://www.topografix.com/GPX/1/1"
     xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1">
 <trk>
  <trkseg>
   <trkpt lat="52.0" lon="4.0">
    <time>2026-06-03T08:00:00Z</time>
    <extensions>
     <power>200</power>
    </extensions>
   </trkpt>
  </trkseg>
 </trk>
</gpx>
"""
    path = tmp_path / "power.gpx"
    path.write_text(xml, encoding="utf-8")

    activity = read_gpx(path)

    assert list(activity.points())[0].power == 200
