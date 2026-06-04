"""Tests for TCX reading and writing."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from gpsmerge.model import Activity, Device, Segment, TrackPoint
from gpsmerge.tcx import read_tcx, write_tcx


def _sample_activity() -> Activity:
    """Build an activity with two segments and a mix of populated/absent channels."""
    first = TrackPoint(
        time=datetime(2026, 6, 3, 12, 0, 0, tzinfo=UTC),
        lat=52.1,
        lon=4.1,
        ele=10.0,
        distance=0.0,
        hr=140,
        cad=85,
        power=220,
        temp=21.5,
        speed=5.5,
    )
    # Every fitness channel absent: must stay None, never become 0.
    second = TrackPoint(
        time=datetime(2026, 6, 3, 12, 0, 1, tzinfo=UTC),
        lat=52.2,
        lon=4.2,
        ele=11.0,
        distance=12.0,
    )
    third = TrackPoint(
        time=datetime(2026, 6, 3, 12, 5, 0, tzinfo=UTC),
        lat=52.3,
        lon=4.3,
        ele=12.0,
        distance=50.0,
        hr=150,
    )
    return Activity(
        sport="cycling",
        device=Device(name="Garmin Edge 530"),
        segments=[Segment(points=[first, second]), Segment(points=[third])],
        time_created=datetime(2026, 6, 3, 11, 0, 0, tzinfo=UTC),
        source_format="tcx",
    )


@pytest.mark.unit
def test_write_then_read_round_trips_every_supported_field(tmp_path: Path) -> None:
    original = _sample_activity()
    path = tmp_path / "out.tcx"

    write_tcx(original, path)
    result = read_tcx(path)

    assert result.sport == original.sport
    assert result.device.name == original.device.name
    assert result.source_format == "tcx"
    assert result.source_path == str(path)

    original_points = list(original.points())
    result_points = list(result.points())
    assert len(result_points) == len(original_points)

    for got, want in zip(result_points, original_points):
        assert got.time == want.time
        assert got.lat == pytest.approx(want.lat)
        assert got.lon == pytest.approx(want.lon)
        assert got.ele == pytest.approx(want.ele)
        assert got.distance == pytest.approx(want.distance)
        assert got.hr == want.hr
        assert got.cad == want.cad
        assert got.power == want.power
        if want.speed is None:
            assert got.speed is None
        else:
            assert got.speed == pytest.approx(want.speed)


@pytest.mark.unit
def test_round_trip_drops_temperature_because_tcx_cannot_store_it(
    tmp_path: Path,
) -> None:
    # The first point carried temp=21.5, but TCX has no temperature field
    # anywhere in its schema, so it cannot survive a write -- a documented loss.
    original = _sample_activity()
    path = tmp_path / "out.tcx"

    write_tcx(original, path)
    result = read_tcx(path)

    assert result.segments[0].points[0].temp is None


@pytest.mark.unit
def test_round_trip_keeps_absent_channels_as_none_not_zero(tmp_path: Path) -> None:
    original = _sample_activity()
    path = tmp_path / "out.tcx"

    write_tcx(original, path)
    result = read_tcx(path)

    # The second point of the first segment had no fitness channels.
    bare = result.segments[0].points[1]
    assert bare.hr is None
    assert bare.cad is None
    assert bare.power is None
    assert bare.temp is None
    assert bare.speed is None


@pytest.mark.unit
def test_round_trip_preserves_segment_boundaries(tmp_path: Path) -> None:
    # Each segment writes as a separate <Track>, so the pause between them
    # survives the round-trip rather than collapsing into one stream of points.
    original = _sample_activity()
    path = tmp_path / "out.tcx"

    write_tcx(original, path)
    result = read_tcx(path)

    assert len(result.segments) == 2
    assert len(result.segments[0].points) == 2
    assert len(result.segments[1].points) == 1


@pytest.mark.unit
def test_sport_round_trips_cycling_as_biking(tmp_path: Path) -> None:
    activity = _sample_activity()
    path = tmp_path / "out.tcx"

    write_tcx(activity, path)

    # The Sport attribute on the wire is Garmin's "Biking" for our "cycling".
    assert 'Sport="Biking"' in path.read_text(encoding="utf-8")
    assert read_tcx(path).sport == "cycling"


@pytest.mark.unit
def test_cross_format_gpx_to_tcx_keeps_supported_channels(tmp_path: Path) -> None:
    # Build an activity as a GPX read would (no per-point distance, with temp),
    # write it as TCX, and confirm the channels TCX supports survive the hop.
    activity = Activity(
        sport="running",
        device=Device(name="Wahoo ELEMNT"),
        segments=[
            Segment(
                points=[
                    TrackPoint(
                        time=datetime(2026, 6, 3, 8, 0, 0, tzinfo=UTC),
                        lat=52.0,
                        lon=4.0,
                        ele=5.0,
                        hr=120,
                        cad=80,
                        power=180,
                        temp=18.0,
                    )
                ]
            )
        ],
    )
    path = tmp_path / "cross.tcx"

    write_tcx(activity, path)
    result = read_tcx(path)

    assert result.sport == "running"
    assert result.device.name == "Wahoo ELEMNT"
    point = list(result.points())[0]
    assert point.lat == pytest.approx(52.0)
    assert point.lon == pytest.approx(4.0)
    assert point.ele == pytest.approx(5.0)
    assert point.hr == 120
    assert point.cad == 80
    assert point.power == 180
    assert point.temp is None  # TCX cannot carry temperature.


@pytest.mark.unit
def test_read_parses_a_hand_written_tcx_with_a_tpx_watts_extension(
    tmp_path: Path,
) -> None:
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<TrainingCenterDatabase
    xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2"
    xmlns:ns3="http://www.garmin.com/xmlschemas/ActivityExtension/v2">
 <Activities>
  <Activity Sport="Biking">
   <Id>2026-06-03T08:00:00Z</Id>
   <Lap StartTime="2026-06-03T08:00:00Z">
    <Track>
     <Trackpoint>
      <Time>2026-06-03T08:00:00Z</Time>
      <Position>
       <LatitudeDegrees>52.0</LatitudeDegrees>
       <LongitudeDegrees>4.0</LongitudeDegrees>
      </Position>
      <AltitudeMeters>5.0</AltitudeMeters>
      <DistanceMeters>0.0</DistanceMeters>
      <HeartRateBpm><Value>120</Value></HeartRateBpm>
      <Cadence>80</Cadence>
      <Extensions>
       <ns3:TPX>
        <ns3:Speed>5.5</ns3:Speed>
        <ns3:Watts>180</ns3:Watts>
       </ns3:TPX>
      </Extensions>
     </Trackpoint>
     <Trackpoint>
      <Time>2026-06-03T08:00:01Z</Time>
      <Position>
       <LatitudeDegrees>52.001</LatitudeDegrees>
       <LongitudeDegrees>4.001</LongitudeDegrees>
      </Position>
     </Trackpoint>
    </Track>
   </Lap>
   <Creator xsi:type="Device_t"
            xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
    <Name>Garmin Edge 530</Name>
   </Creator>
  </Activity>
 </Activities>
</TrainingCenterDatabase>
"""
    path = tmp_path / "hand.tcx"
    path.write_text(xml, encoding="utf-8")

    activity = read_tcx(path)

    assert activity.sport == "cycling"
    assert activity.device.name == "Garmin Edge 530"
    points = list(activity.points())
    assert points[0].time == datetime(2026, 6, 3, 8, 0, 0, tzinfo=UTC)
    assert points[0].lat == pytest.approx(52.0)
    assert points[0].ele == pytest.approx(5.0)
    assert points[0].distance == pytest.approx(0.0)
    assert points[0].hr == 120
    assert points[0].cad == 80
    assert points[0].power == 180  # from the TPX Watts extension
    assert points[0].speed == pytest.approx(5.5)
    # The second point carries no sensors: channels are None, not 0.
    assert points[1].hr is None
    assert points[1].power is None


@pytest.mark.unit
def test_read_tolerates_float_formatted_integer_channels(tmp_path: Path) -> None:
    # Some exporters write whole-number channels with a trailing ".0"; read them.
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<TrainingCenterDatabase
    xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2">
 <Activities>
  <Activity Sport="Biking">
   <Id>2026-06-03T08:00:00Z</Id>
   <Lap StartTime="2026-06-03T08:00:00Z">
    <Track>
     <Trackpoint>
      <Time>2026-06-03T08:00:00Z</Time>
      <HeartRateBpm><Value>140.0</Value></HeartRateBpm>
      <Cadence>85.0</Cadence>
     </Trackpoint>
    </Track>
   </Lap>
  </Activity>
 </Activities>
</TrainingCenterDatabase>
"""
    path = tmp_path / "floats.tcx"
    path.write_text(xml, encoding="utf-8")

    point = next(iter(read_tcx(path).points()))
    assert point.hr == 140
    assert point.cad == 85
