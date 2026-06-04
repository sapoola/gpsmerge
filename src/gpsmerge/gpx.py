"""Read and write GPX activity files via gpxpy.

GPX is the lingua franca of GPS tools: every device and site reads it. The
fitness channels (heart rate, cadence, power, temperature) live in Garmin's
extension namespaces rather than core GPX, so most of this module is the small
amount of glue that moves those channels between our model and the extension
XML that Strava and Garmin emit.

Two namespaces carry the channels:

- ``gpxtpx`` (Garmin TrackPointExtension) wraps ``hr``, ``cad`` and ``atemp``
  (temperature) under a single ``TrackPointExtension`` element.
- ``gpxpx`` (Garmin PowerExtension) carries ``PowerInWatts`` as a sibling.

On read we match extension elements by their **local name** (the tag with any
namespace stripped), so a file is understood whether its author used the
``gpxtpx:`` prefix, a different prefix, or none at all.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import gpxpy
import gpxpy.gpx
from lxml import etree

from gpsmerge.model import Activity, Device, Segment, TrackPoint

# Garmin extension namespaces, written exactly as Strava/Garmin produce them.
# These are XML *namespace identifiers*, not URLs to fetch -- a browser 404 on
# them is expected. They must match these exact strings or HR/power round-trips
# break. test_namespaces.py pins them.
TRACKPOINT_EXTENSION_NS = "http://www.garmin.com/xmlschemas/TrackPointExtension/v1"
POWER_EXTENSION_NS = "http://www.garmin.com/xmlschemas/PowerExtension/v1"

# Local names we recognise inside a point's <extensions> block.
_HR_TAG = "hr"
_CADENCE_TAG = "cad"
_TEMPERATURE_TAG = "atemp"
_POWER_WATTS_TAG = "PowerInWatts"  # gpxpx flavour
_POWER_PLAIN_TAG = "power"  # bare <power> some writers emit

# Used for the GPX creator attribute when an activity has no device name.
_DEFAULT_CREATOR = "gpsmerge"

SOURCE_FORMAT = "gpx"


def read_gpx(path: Path) -> Activity:
    """Parse a GPX file into the canonical :class:`Activity`.

    Each GPX track contributes its segments; each ``<trkseg>`` becomes a
    :class:`Segment` and each ``<trkpt>`` a :class:`TrackPoint`. Fitness
    channels are read from the Garmin TrackPointExtension and PowerExtension.

    Parameters
    ----------
    path : Path
        Path to a GPX file.

    Returns
    -------
    Activity
        The parsed activity, with ``source_format="gpx"`` and ``source_path``
        set to ``str(path)``. Absent channels are ``None``, never ``0``, and
        every timestamp is timezone-aware UTC.
    """
    with path.open(encoding="utf-8") as handle:
        gpx = gpxpy.parse(handle)

    segments: list[Segment] = []
    sport: str | None = None
    name: str | None = None
    for track in gpx.tracks:
        if sport is None and track.type:
            sport = track.type.lower()
        if name is None and track.name:
            name = track.name
        for gpx_segment in track.segments:
            points = [_read_point(p) for p in gpx_segment.points]
            segments.append(Segment(points=points))

    return Activity(
        sport=sport,
        name=name or gpx.name,
        device=Device(name=gpx.creator),
        segments=segments,
        time_created=_to_utc(gpx.time),
        source_format=SOURCE_FORMAT,
        source_path=str(path),
    )


def _read_point(gpx_point: gpxpy.gpx.GPXTrackPoint) -> TrackPoint:
    """Convert one gpxpy track point, including its extensions, to a TrackPoint."""
    hr: int | None = None
    cad: int | None = None
    power: int | None = None
    temp: float | None = None

    # The gpxtpx wrapper nests hr/cad/atemp one level deep, while gpxpx power and
    # the bare <power> sit at the top level, so walk both levels uniformly. Match
    # by local name so any prefix (or none) is understood.
    for element in _iter_with_children(gpx_point.extensions):
        if element.text is None:
            continue
        name = _local_name(element.tag)
        if name == _HR_TAG:
            hr = int(element.text)
        elif name == _CADENCE_TAG:
            cad = int(element.text)
        elif name == _TEMPERATURE_TAG:
            temp = float(element.text)
        elif name in (_POWER_WATTS_TAG, _POWER_PLAIN_TAG):
            power = int(element.text)

    return TrackPoint(
        time=_to_utc(gpx_point.time),
        lat=gpx_point.latitude,
        lon=gpx_point.longitude,
        ele=gpx_point.elevation,
        hr=hr,
        cad=cad,
        power=power,
        temp=temp,
        speed=gpx_point.speed,
    )


def _iter_with_children(
    extensions: list[etree._Element],
) -> list[etree._Element]:
    """Return each extension element followed by its direct children, flattened.

    The Garmin TrackPointExtension nests hr/cad/atemp one level deep, so a flat
    walk of top-level extensions alone would miss them.
    """
    elements: list[etree._Element] = []
    for element in extensions:
        elements.append(element)
        elements.extend(element)
    return elements


def _local_name(tag: str) -> str:
    """Strip any ``{namespace}`` prefix from an lxml tag, leaving the local name."""
    return tag.rpartition("}")[2]


def _to_utc(value: datetime | None) -> datetime | None:
    """Return `value` as timezone-aware UTC, or ``None`` if it is ``None``."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def write_gpx(activity: Activity, path: Path) -> None:
    """Write an :class:`Activity` to a Strava-compatible GPX file.

    Emits one ``<trk>`` with one ``<trkseg>`` per segment. Fitness channels are
    attached under the Garmin TrackPointExtension (hr/cad/atemp) and
    PowerExtension (PowerInWatts) namespaces, and only for channels that are
    not ``None`` -- a missing channel is omitted, never written as ``0``.

    Three kinds of data cannot survive a GPX round-trip and are intentionally not
    written: the structured :class:`Device` identity (manufacturer, product,
    serial number, software version) -- GPX has only a single ``creator`` string,
    so only ``device.name`` is kept; per-point ``distance`` -- GPX has no
    trackpoint distance element, so distance is recomputed from positions via
    :func:`gpsmerge.geo.cumulative_distance` when needed rather than stored; and
    per-point ``speed`` -- GPX 1.1, which we emit because it is the version that
    carries the Garmin fitness extensions, has no trackpoint speed element.
    (``read_gpx`` still reads ``speed`` from older GPX 1.0 files that include it.)

    Parameters
    ----------
    activity : Activity
        The activity to serialise.
    path : Path
        Destination file path; overwritten if it exists.
    """
    gpx = gpxpy.gpx.GPX()
    gpx.creator = activity.device.name or _DEFAULT_CREATOR
    gpx.name = activity.name
    gpx.time = activity.time_created
    # Register the prefixes so the output validates the way Garmin produces it.
    gpx.nsmap["gpxtpx"] = TRACKPOINT_EXTENSION_NS
    gpx.nsmap["gpxpx"] = POWER_EXTENSION_NS

    track = gpxpy.gpx.GPXTrack()
    track.name = activity.name
    # gpxpy ships no type stubs, so mypy infers GPXTrack.type as None-only from
    # its default; the attribute does accept the sport string.
    track.type = activity.sport  # type: ignore[assignment]
    gpx.tracks.append(track)

    for segment in activity.segments:
        gpx_segment = gpxpy.gpx.GPXTrackSegment()
        track.segments.append(gpx_segment)
        for point in segment.points:
            gpx_segment.points.append(_write_point(point))

    path.write_text(gpx.to_xml(), encoding="utf-8")


def _write_point(point: TrackPoint) -> gpxpy.gpx.GPXTrackPoint:
    """Build a gpxpy track point, attaching extensions for non-None channels."""
    gpx_point = gpxpy.gpx.GPXTrackPoint(
        latitude=point.lat,
        longitude=point.lon,
        elevation=point.ele,
        time=point.time,
    )

    tpe = _build_trackpoint_extension(point)
    if tpe is not None:
        gpx_point.extensions.append(tpe)

    if point.power is not None:
        gpx_point.extensions.append(_build_power_extension(point.power))

    return gpx_point


def _build_trackpoint_extension(point: TrackPoint) -> etree._Element | None:
    """Build the gpxtpx wrapper for hr/cad/atemp, or ``None`` if all are absent."""
    fields = {
        _HR_TAG: point.hr,
        _CADENCE_TAG: point.cad,
        _TEMPERATURE_TAG: point.temp,
    }
    if all(value is None for value in fields.values()):
        return None

    wrapper = etree.Element(
        _qualified(TRACKPOINT_EXTENSION_NS, "TrackPointExtension"),
        nsmap={"gpxtpx": TRACKPOINT_EXTENSION_NS},
    )
    for name, value in fields.items():
        if value is None:
            continue
        child = etree.SubElement(wrapper, _qualified(TRACKPOINT_EXTENSION_NS, name))
        child.text = _format_channel(value)
    return wrapper


def _build_power_extension(power: int) -> etree._Element:
    """Build the gpxpx PowerInWatts element for a recorded power value."""
    element = etree.Element(
        _qualified(POWER_EXTENSION_NS, _POWER_WATTS_TAG),
        nsmap={"gpxpx": POWER_EXTENSION_NS},
    )
    element.text = str(power)
    return element


def _format_channel(value: float | int) -> str:
    """Render a channel value: integers without a trailing ``.0``, floats as-is."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _qualified(namespace: str, local_name: str) -> str:
    """Return an lxml Clark-notation tag ``{namespace}local_name``."""
    return f"{{{namespace}}}{local_name}"
