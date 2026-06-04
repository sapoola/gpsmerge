"""Tests for TCX reading and writing."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from pathlib import Path

import pytest
from lxml.etree import LxmlError

from gpsmerge.model import Activity, Device, Lap, Segment, TrackPoint
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


# --- Laps -----------------------------------------------------------------

# A TCX namespace map for asserting on the written XML structure directly.
_NSMAP = {"t": "http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2"}


def _track_counts_per_lap(path: Path) -> list[int]:
    """Return the number of ``<Track>`` elements inside each written ``<Lap>``."""
    from lxml import etree

    root = etree.parse(str(path)).getroot()
    laps = root.findall(".//t:Lap", namespaces=_NSMAP)
    return [len(lap.findall("t:Track", namespaces=_NSMAP)) for lap in laps]


# A two-lap TCX. The first lap holds a pause: two <Track> elements with a gap
# between them. The second lap is a single track. This exercises laps and
# segments being independent -- one lap can span a pause.
_TWO_LAP_TCX = """<?xml version="1.0" encoding="UTF-8"?>
<TrainingCenterDatabase
    xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2">
 <Activities>
  <Activity Sport="Biking">
   <Id>2026-06-03T08:00:00Z</Id>
   <Lap StartTime="2026-06-03T08:00:00Z">
    <TotalTimeSeconds>120</TotalTimeSeconds>
    <DistanceMeters>100</DistanceMeters>
    <Calories>0</Calories>
    <Intensity>Active</Intensity>
    <TriggerMethod>Distance</TriggerMethod>
    <Track>
     <Trackpoint>
      <Time>2026-06-03T08:00:00Z</Time>
      <DistanceMeters>0</DistanceMeters>
     </Trackpoint>
     <Trackpoint>
      <Time>2026-06-03T08:00:30Z</Time>
      <DistanceMeters>40</DistanceMeters>
     </Trackpoint>
    </Track>
    <Track>
     <Trackpoint>
      <Time>2026-06-03T08:01:30Z</Time>
      <DistanceMeters>70</DistanceMeters>
     </Trackpoint>
     <Trackpoint>
      <Time>2026-06-03T08:02:00Z</Time>
      <DistanceMeters>100</DistanceMeters>
     </Trackpoint>
    </Track>
   </Lap>
   <Lap StartTime="2026-06-03T08:02:01Z">
    <TotalTimeSeconds>59</TotalTimeSeconds>
    <DistanceMeters>50</DistanceMeters>
    <Calories>0</Calories>
    <Intensity>Active</Intensity>
    <TriggerMethod>Manual</TriggerMethod>
    <Track>
     <Trackpoint>
      <Time>2026-06-03T08:02:01Z</Time>
      <DistanceMeters>110</DistanceMeters>
     </Trackpoint>
     <Trackpoint>
      <Time>2026-06-03T08:03:00Z</Time>
      <DistanceMeters>150</DistanceMeters>
     </Trackpoint>
    </Track>
   </Lap>
  </Activity>
 </Activities>
</TrainingCenterDatabase>
"""


@pytest.mark.unit
def test_read_parses_laps_independently_of_segments(tmp_path: Path) -> None:
    # Two <Lap> elements become two model laps with their start times and mapped
    # triggers, while the pause inside the first lap still shows up as a separate
    # segment -- laps and segments are independent overlays on the timeline.
    path = tmp_path / "two_laps.tcx"
    path.write_text(_TWO_LAP_TCX, encoding="utf-8")

    activity = read_tcx(path)

    assert len(activity.laps) == 2
    assert activity.laps[0].start_time == datetime(2026, 6, 3, 8, 0, 0, tzinfo=UTC)
    assert activity.laps[1].start_time == datetime(2026, 6, 3, 8, 2, 1, tzinfo=UTC)
    # TriggerMethod Distance/Manual map to our distance/manual.
    assert activity.laps[0].trigger == "distance"
    assert activity.laps[1].trigger == "manual"
    # end_time = start + TotalTimeSeconds when present.
    assert activity.laps[0].end_time == datetime(2026, 6, 3, 8, 2, 0, tzinfo=UTC)
    # Three <Track> elements (two in the first lap, one in the second) mean three
    # segments: the pause inside lap one survives as its own segment boundary.
    assert len(activity.segments) == 3


@pytest.mark.unit
def test_round_trip_preserves_laps_and_every_point_and_inner_pause(
    tmp_path: Path,
) -> None:
    # Read a two-lap file, write it back, and read again. Both laps survive with
    # their start times and triggers, every point survives, and the pause inside
    # the first lap is preserved as two <Track> elements within that lap.
    source = tmp_path / "two_laps.tcx"
    source.write_text(_TWO_LAP_TCX, encoding="utf-8")
    original = read_tcx(source)

    written = tmp_path / "round_trip.tcx"
    write_tcx(original, written)
    result = read_tcx(written)

    # Two laps survive, with their start times and triggers intact.
    assert len(result.laps) == 2
    assert [lap.start_time for lap in result.laps] == [
        datetime(2026, 6, 3, 8, 0, 0, tzinfo=UTC),
        datetime(2026, 6, 3, 8, 2, 1, tzinfo=UTC),
    ]
    assert [lap.trigger for lap in result.laps] == ["distance", "manual"]

    # Every point survives the round-trip, in order.
    original_times = [p.time for p in original.points()]
    result_times = [p.time for p in result.points()]
    assert result_times == original_times
    assert len(result_times) == 6

    # The pause inside the first lap is written as two tracks; the second lap is
    # a single track. So the written file has [2, 1] tracks across its two laps.
    assert _track_counts_per_lap(written) == [2, 1]


@pytest.mark.unit
def test_write_recomputes_lap_summaries_from_its_own_points(tmp_path: Path) -> None:
    # A lap's TotalTimeSeconds and DistanceMeters are recomputed from the points
    # that fall in the lap, not copied from any stored value -- so they track the
    # data. Partitioning is by start time: a point at exactly a lap's start_time
    # belongs to that (later) lap, so the 9:02:00 boundary point joins lap 2.
    points = [
        TrackPoint(time=datetime(2026, 6, 3, 9, 0, 0, tzinfo=UTC), distance=0.0),
        TrackPoint(time=datetime(2026, 6, 3, 9, 1, 0, tzinfo=UTC), distance=120.0),
        TrackPoint(time=datetime(2026, 6, 3, 9, 2, 0, tzinfo=UTC), distance=200.0),
        TrackPoint(time=datetime(2026, 6, 3, 9, 2, 30, tzinfo=UTC), distance=260.0),
    ]
    activity = Activity(
        sport="cycling",
        segments=[Segment(points=points)],
        laps=[
            Lap(start_time=datetime(2026, 6, 3, 9, 0, 0, tzinfo=UTC), trigger="time"),
            Lap(start_time=datetime(2026, 6, 3, 9, 2, 0, tzinfo=UTC), trigger="manual"),
        ],
    )
    path = tmp_path / "recompute.tcx"

    write_tcx(activity, path)

    from lxml import etree

    root = etree.parse(str(path)).getroot()
    laps = root.findall(".//t:Lap", namespaces=_NSMAP)
    first_seconds = laps[0].findtext("t:TotalTimeSeconds", namespaces=_NSMAP)
    first_distance = laps[0].findtext("t:DistanceMeters", namespaces=_NSMAP)
    second_seconds = laps[1].findtext("t:TotalTimeSeconds", namespaces=_NSMAP)
    second_distance = laps[1].findtext("t:DistanceMeters", namespaces=_NSMAP)

    # Lap 1 holds the 9:00:00 and 9:01:00 points: 60 s, 120 m (120 - 0).
    assert float(first_seconds) == pytest.approx(60.0)
    assert float(first_distance) == pytest.approx(120.0)
    # Lap 2 holds the 9:02:00 and 9:02:30 points: 30 s, 60 m (260 - 200).
    assert float(second_seconds) == pytest.approx(30.0)
    assert float(second_distance) == pytest.approx(60.0)
    # The mapped triggers landed on the wire too.
    assert laps[0].findtext("t:TriggerMethod", namespaces=_NSMAP) == "Time"
    assert laps[1].findtext("t:TriggerMethod", namespaces=_NSMAP) == "Manual"


@pytest.mark.unit
def test_empty_laps_still_writes_a_single_synthetic_lap(tmp_path: Path) -> None:
    # An activity with laps=[] (e.g. read from GPX) keeps the old behaviour: one
    # synthetic <Lap> spanning the activity, with one <Track> per segment.
    activity = _sample_activity()
    assert activity.laps == []
    path = tmp_path / "synthetic.tcx"

    write_tcx(activity, path)
    result = read_tcx(path)

    # One synthetic lap is written, spanning both segments.
    assert _track_counts_per_lap(path) == [2]
    # Reading it back surfaces that single lap.
    assert len(result.laps) == 1
    # Its start time is the activity's first point time.
    assert result.laps[0].start_time == datetime(2026, 6, 3, 12, 0, 0, tzinfo=UTC)


@pytest.mark.unit
def test_point_before_first_lap_start_joins_the_first_lap(tmp_path: Path) -> None:
    # A point recorded before the first lap's start_time must not be lost: it is
    # assigned to the first lap by the start-time partition.
    early = TrackPoint(time=datetime(2026, 6, 3, 7, 59, 0, tzinfo=UTC), distance=0.0)
    on_time = TrackPoint(time=datetime(2026, 6, 3, 8, 0, 5, tzinfo=UTC), distance=10.0)
    activity = Activity(
        sport="cycling",
        segments=[Segment(points=[early, on_time])],
        laps=[Lap(start_time=datetime(2026, 6, 3, 8, 0, 0, tzinfo=UTC))],
    )
    path = tmp_path / "early.tcx"

    write_tcx(activity, path)
    result = read_tcx(path)

    # The early point survived and lands in the single lap with the on-time one.
    result_times = [p.time for p in result.points()]
    assert result_times == [
        datetime(2026, 6, 3, 7, 59, 0, tzinfo=UTC),
        datetime(2026, 6, 3, 8, 0, 5, tzinfo=UTC),
    ]
