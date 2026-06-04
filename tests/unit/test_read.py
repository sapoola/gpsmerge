"""Tests for the format-dispatch reader."""

from __future__ import annotations

from pathlib import Path

import pytest

from gpsmerge.errors import ParseError, UnsupportedFormat
from gpsmerge.read import read

_MINIMAL_GPX = """<?xml version="1.0" encoding="UTF-8"?>
<gpx creator="Device" version="1.1" xmlns="http://www.topografix.com/GPX/1/1">
 <trk><trkseg>
  <trkpt lat="52.0" lon="4.0"><time>2026-06-04T08:00:00Z</time></trkpt>
 </trkseg></trk>
</gpx>
"""

_MINIMAL_TCX = """<?xml version="1.0" encoding="UTF-8"?>
<TrainingCenterDatabase
    xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2">
 <Activities><Activity Sport="Biking">
  <Id>2026-06-04T08:00:00Z</Id>
  <Lap StartTime="2026-06-04T08:00:00Z"><Track>
   <Trackpoint><Time>2026-06-04T08:00:00Z</Time>
    <Position><LatitudeDegrees>52.0</LatitudeDegrees>
    <LongitudeDegrees>4.0</LongitudeDegrees></Position>
   </Trackpoint>
  </Track></Lap>
 </Activity></Activities>
</TrainingCenterDatabase>
"""


@pytest.mark.unit
def test_read_dispatches_gpx(tmp_path: Path) -> None:
    path = tmp_path / "ride.gpx"
    path.write_text(_MINIMAL_GPX, encoding="utf-8")
    assert read(path).source_format == "gpx"


@pytest.mark.unit
def test_read_dispatches_tcx(tmp_path: Path) -> None:
    path = tmp_path / "ride.tcx"
    path.write_text(_MINIMAL_TCX, encoding="utf-8")
    assert read(path).source_format == "tcx"


@pytest.mark.unit
def test_read_rejects_an_unsupported_extension() -> None:
    # FIT reading arrives in a later milestone.
    with pytest.raises(UnsupportedFormat):
        read(Path("ride.fit"))


@pytest.mark.unit
def test_read_rejects_an_unknown_extension() -> None:
    with pytest.raises(UnsupportedFormat):
        read(Path("ride.foo"))


@pytest.mark.unit
def test_read_wraps_malformed_xml_in_a_parse_error(tmp_path: Path) -> None:
    path = tmp_path / "broken.gpx"
    path.write_text("this is not valid gpx <<<", encoding="utf-8")
    with pytest.raises(ParseError):
        read(path)
