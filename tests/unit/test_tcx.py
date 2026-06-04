"""Tests for TCX reading and writing."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from pathlib import Path

import pytest
from lxml.etree import LxmlError

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


@pytest.mark.unit
def test_read_does_not_resolve_external_or_nested_entities(tmp_path: Path) -> None:
    # A malicious TCX with a DOCTYPE declaring an external/general entity (XXE)
    # plus a billion-laughs nested-entity bomb. A hardened parser must neither
    # read the local file the entity points at, nor expand the bomb. We accept
    # either a clean parse with the entities left unresolved, or a ValueError --
    # what we must NOT allow is exfiltration of the secret or a memory blow-up.
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET", encoding="utf-8")
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE TrainingCenterDatabase [
  <!ENTITY xxe SYSTEM "file://{secret}">
  <!ENTITY lol "lol">
  <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
  <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">
  <!ENTITY lol4 "&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;">
]>
<TrainingCenterDatabase
    xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2">
 <Activities>
  <Activity Sport="Biking">
   <Id>2026-06-03T08:00:00Z&xxe;&lol4;</Id>
   <Lap StartTime="2026-06-03T08:00:00Z">
    <Track>
     <Trackpoint><Time>2026-06-03T08:00:00Z</Time></Trackpoint>
    </Track>
   </Lap>
  </Activity>
 </Activities>
</TrainingCenterDatabase>
"""
    path = tmp_path / "evil.tcx"
    path.write_text(xml, encoding="utf-8")

    try:
        activity = read_tcx(path)
    except (LxmlError, ValueError):
        # Refusing the entity-laden document outright is an acceptable outcome:
        # with the DTD never loaded, the undefined entities trip a parse error
        # rather than expanding. The point is that nothing was resolved.
        return

    # If it parsed, the secret must not have leaked into any text we read.
    for point in activity.points():
        if point.time is not None:
            assert "TOP-SECRET" not in point.time.isoformat()
    rendered = "".join(filter(None, (str(s.points) for s in activity.segments)))
    assert "TOP-SECRET" not in rendered


@pytest.mark.unit
def test_read_concatenates_points_from_every_activity(tmp_path: Path) -> None:
    # Multisport / Training Center files can hold several <Activity> elements.
    # Every Activity's points must come through, not just the first one's.
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<TrainingCenterDatabase
    xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2">
 <Activities>
  <Activity Sport="Running">
   <Id>2026-06-03T08:00:00Z</Id>
   <Lap StartTime="2026-06-03T08:00:00Z">
    <Track>
     <Trackpoint><Time>2026-06-03T08:00:00Z</Time></Trackpoint>
     <Trackpoint><Time>2026-06-03T08:00:01Z</Time></Trackpoint>
    </Track>
   </Lap>
  </Activity>
  <Activity Sport="Biking">
   <Id>2026-06-03T08:30:00Z</Id>
   <Lap StartTime="2026-06-03T08:30:00Z">
    <Track>
     <Trackpoint><Time>2026-06-03T08:30:00Z</Time></Trackpoint>
    </Track>
   </Lap>
  </Activity>
 </Activities>
</TrainingCenterDatabase>
"""
    path = tmp_path / "multisport.tcx"
    path.write_text(xml, encoding="utf-8")

    activity = read_tcx(path)

    times = [p.time for p in activity.points()]
    assert times == [
        datetime(2026, 6, 3, 8, 0, 0, tzinfo=UTC),
        datetime(2026, 6, 3, 8, 0, 1, tzinfo=UTC),
        datetime(2026, 6, 3, 8, 30, 0, tzinfo=UTC),
    ]
    # Sport/device/Id come from the first Activity.
    assert activity.sport == "running"


@pytest.mark.unit
def test_round_trip_preserves_sub_second_timestamps(tmp_path: Path) -> None:
    # Time is the merge/overlap key, so a fractional second must survive a write.
    sub_second = datetime(2026, 6, 3, 12, 0, 0, 250000, tzinfo=UTC)
    activity = Activity(
        sport="cycling",
        segments=[Segment(points=[TrackPoint(time=sub_second, lat=52.0, lon=4.0)])],
    )
    path = tmp_path / "subsecond.tcx"

    write_tcx(activity, path)

    # The fractional second must appear on the wire, milliseconds and all.
    assert "2026-06-03T12:00:00.250Z" in path.read_text(encoding="utf-8")
    assert next(iter(read_tcx(path).points())).time == sub_second


@pytest.mark.unit
def test_write_emits_whole_seconds_when_no_microseconds(tmp_path: Path) -> None:
    # A whole-second time must NOT gain a spurious ".000" fractional part.
    whole = datetime(2026, 6, 3, 12, 0, 0, tzinfo=UTC)
    activity = Activity(
        segments=[Segment(points=[TrackPoint(time=whole, lat=52.0, lon=4.0)])],
    )
    path = tmp_path / "whole.tcx"

    write_tcx(activity, path)

    text = path.read_text(encoding="utf-8")
    assert "<Time>2026-06-03T12:00:00Z</Time>" in text


@pytest.mark.unit
def test_round_trip_preserves_a_lone_latitude(tmp_path: Path) -> None:
    # A point with latitude but no longitude must not silently drop the latitude
    # on a TCX->TCX round-trip: <Position> is emitted when EITHER is present.
    activity = Activity(
        segments=[
            Segment(
                points=[
                    TrackPoint(
                        time=datetime(2026, 6, 3, 12, 0, 0, tzinfo=UTC),
                        lat=52.123,
                        lon=None,
                    )
                ]
            )
        ],
    )
    path = tmp_path / "lone_lat.tcx"

    write_tcx(activity, path)
    result = next(iter(read_tcx(path).points()))

    assert result.lat == pytest.approx(52.123)
    assert result.lon is None


@pytest.mark.unit
@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_write_treats_non_finite_channel_values_as_absent(
    tmp_path: Path, bad: float
) -> None:
    # A NaN/inf value would serialise as the schema-invalid literal "nan"/"inf".
    # The writer must treat any non-finite value as absent instead.
    activity = Activity(
        segments=[
            Segment(
                points=[
                    TrackPoint(
                        time=datetime(2026, 6, 3, 12, 0, 0, tzinfo=UTC),
                        lat=bad,
                        lon=4.0,
                        ele=bad,
                        distance=bad,
                        speed=bad,
                    )
                ]
            )
        ],
    )
    path = tmp_path / "nonfinite.tcx"

    write_tcx(activity, path)

    text = path.read_text(encoding="utf-8")
    assert "nan" not in text.lower()
    assert "inf" not in text.lower()
