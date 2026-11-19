"""Pin the XML namespace identifiers gpsmerge reads and writes.

These constants are XML *namespace identifiers* -- unique strings that label which
vocabulary an element belongs to. They are deliberately NOT URLs you fetch, so a
browser 404 on them is expected and means nothing. Garmin, Strava, and every
GPX/TCX file use these exact strings; if one ever drifts, gpsmerge would silently
stop recognising or producing standard files. These tests fail loudly on any
change. (The downloadable Garmin schema documents -- e.g.
https://www8.garmin.com/xmlschemas/TrainingCenterDatabasev2.xsd -- are a separate
thing, referenced via xsi:schemaLocation, not the namespace.)
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from gpsmerge.io import gpx, tcx
from gpsmerge.io.read import read
from gpsmerge.model import Activity, Device, Segment, TrackPoint

# The canonical namespace strings, verified against Garmin's published schemas.
GPX_1_1_NS = "http://www.topografix.com/GPX/1/1"

# The resolvable (HTTP 200) schema documents we advertise via xsi:schemaLocation.
GPX_SCHEMA_URL = "http://www.topografix.com/GPX/1/1/gpx.xsd"
TCX_SCHEMA_URL = "https://www8.garmin.com/xmlschemas/TrainingCenterDatabasev2.xsd"

_PINNED = {
    "gpx.TRACKPOINT_EXTENSION_NS": (
        gpx.TRACKPOINT_EXTENSION_NS,
        "http://www.garmin.com/xmlschemas/TrackPointExtension/v1",
    ),
    "gpx.POWER_EXTENSION_NS": (
        gpx.POWER_EXTENSION_NS,
        "http://www.garmin.com/xmlschemas/PowerExtension/v1",
    ),
    "tcx.TCX_NS": (
        tcx.TCX_NS,
        "http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2",
    ),
    "tcx.TPX_NS": (
        tcx.TPX_NS,
        "http://www.garmin.com/xmlschemas/ActivityExtension/v2",
    ),
    "tcx._XSI_NS": (
        tcx._XSI_NS,
        "http://www.w3.org/2001/XMLSchema-instance",
    ),
}


@pytest.mark.unit
@pytest.mark.parametrize("name", list(_PINNED))
def test_namespace_constant_matches_its_canonical_value(name: str) -> None:
    actual, expected = _PINNED[name]
    assert actual == expected, (
        f"{name} changed to {actual!r}; XML namespaces are fixed identifiers, "
        f"not URLs -- it must stay {expected!r} or standard files break."
    )


def _activity_with_sensors() -> Activity:
    point = TrackPoint(
        time=datetime(2026, 6, 4, 8, 0, 0, tzinfo=UTC),
        lat=52.0,
        lon=4.0,
        ele=10.0,
        hr=140,
        power=220,
    )
    return Activity(
        sport="cycling",
        device=Device(name="Garmin Edge 530"),
        segments=[Segment(points=[point])],
    )


@pytest.mark.unit
def test_written_gpx_uses_the_canonical_namespaces(tmp_path: Path) -> None:
    path = tmp_path / "out.gpx"
    gpx.write_gpx(_activity_with_sensors(), path)
    text = path.read_text(encoding="utf-8")
    assert GPX_1_1_NS in text
    assert gpx.TRACKPOINT_EXTENSION_NS in text  # heart rate lives here
    assert gpx.POWER_EXTENSION_NS in text  # power lives here


@pytest.mark.unit
def test_written_tcx_uses_the_canonical_namespaces(tmp_path: Path) -> None:
    path = tmp_path / "out.tcx"
    tcx.write_tcx(_activity_with_sensors(), path)
    text = path.read_text(encoding="utf-8")
    assert tcx.TCX_NS in text
    assert tcx.TPX_NS in text  # power lives in the TPX extension


@pytest.mark.unit
def test_written_gpx_advertises_resolvable_schema_locations(tmp_path: Path) -> None:
    path = tmp_path / "out.gpx"
    gpx.write_gpx(_activity_with_sensors(), path)
    text = path.read_text(encoding="utf-8")
    assert "schemaLocation" in text  # xsi:schemaLocation on the root
    assert GPX_SCHEMA_URL in text  # GPX base schema, verified HTTP 200


@pytest.mark.unit
def test_written_tcx_advertises_resolvable_schema_locations(tmp_path: Path) -> None:
    path = tmp_path / "out.tcx"
    tcx.write_tcx(_activity_with_sensors(), path)
    text = path.read_text(encoding="utf-8")
    assert "schemaLocation" in text  # xsi:schemaLocation on the root
    assert TCX_SCHEMA_URL in text  # TCX schema on the resolvable www8 host


@pytest.mark.unit
def test_written_gpx_still_round_trips_with_schema_locations(tmp_path: Path) -> None:
    # Adding xsi:schemaLocation must not break parsing: channels survive the trip.
    path = tmp_path / "out.gpx"
    gpx.write_gpx(_activity_with_sensors(), path)
    point = next(read(path).points())
    assert point.hr == 140
    assert point.power == 220


@pytest.mark.unit
def test_written_tcx_still_round_trips_with_schema_locations(tmp_path: Path) -> None:
    # Adding xsi:schemaLocation must not break parsing: channels survive the trip.
    path = tmp_path / "out.tcx"
    tcx.write_tcx(_activity_with_sensors(), path)
    point = next(read(path).points())
    assert point.hr == 140
    assert point.power == 220
