"""Read and write TCX (Garmin Training Center XML) activity files via lxml.

TCX is Garmin's older XML format. Unlike GPX's bare list of points, TCX is built
around laps: an ``<Activity>`` holds one or more ``<Lap>`` elements, and the
trackpoints live inside a lap under a ``<Track>``. We do not model laps yet, so on
read we flatten every lap's tracks into one segment list, and on write we emit a
single lap whose tracks are our segments. Each ``<Track>`` is one continuous run
of recording, so the gap between two tracks is a pause -- exactly our
:class:`~gpsmerge.model.Segment` boundary.

Two namespaces carry the data:

- the TCX namespace itself holds position, time, altitude, distance, heart rate,
  and cadence directly in the schema (no extension hunting, unlike GPX).
- the Garmin ActivityExtension (TPX) namespace carries the channels TCX core
  lacks: ``Watts`` (power), ``Speed``, and ``RunCadence`` (running cadence).

On read we match elements by their namespace URI plus local name, never by prefix
string, so a file is understood whichever prefix its author chose.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from lxml import etree

from gpsmerge.model import Activity, Device, Segment, TrackPoint
from gpsmerge.timeutil import parse_time

# Garmin namespaces, written exactly as Garmin Training Center produces them.
# IMPORTANT: these are XML *namespace identifiers*, not URLs to fetch -- a browser
# 404 on them is expected and irrelevant. They must match these exact strings or
# real TCX files stop being recognised. (The downloadable schema lives separately
# at https://www8.garmin.com/xmlschemas/TrainingCenterDatabasev2.xsd, which is a
# schema-location, not the namespace.) test_namespaces.py pins these.
TCX_NS = "http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2"
TPX_NS = "http://www.garmin.com/xmlschemas/ActivityExtension/v2"
_XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"

# Map the TCX Sport attribute to our canonical lowercase sport, and back. Any
# Sport we do not recognise reads as "other"; any sport we cannot name writes as
# "Other", TCX's catch-all.
_SPORT_FROM_TCX = {"Biking": "cycling", "Running": "running"}
_SPORT_TO_TCX = {"cycling": "Biking", "running": "Running"}
_DEFAULT_SPORT = "other"
_TCX_OTHER_SPORT = "Other"

# Used for the <Creator><Name> when an activity has no device name.
_DEFAULT_CREATOR = "gpsmerge"

# ISO-8601 in UTC with a trailing Z, the form TCX timestamps take. Whole seconds:
# the formats we read record at one-second resolution, so we lose nothing.
_TCX_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_EPOCH_TIME = "1970-01-01T00:00:00Z"

SOURCE_FORMAT = "tcx"


def read_tcx(path: Path) -> Activity:
    """Parse a TCX file into the canonical :class:`Activity`.

    The first ``<Activity>`` is read. Its ``Sport`` attribute becomes the
    canonical sport, every ``<Track>`` (across all laps, in order) becomes a
    :class:`Segment`, and every ``<Trackpoint>`` a :class:`TrackPoint`. Power,
    speed, and running cadence are read from the Garmin ActivityExtension.

    Parameters
    ----------
    path : Path
        Path to a TCX file.

    Returns
    -------
    Activity
        The parsed activity, with ``source_format="tcx"`` and ``source_path``
        set to ``str(path)``. Absent channels are ``None``, never ``0``, and
        every timestamp is timezone-aware UTC.
    """
    tree = etree.parse(str(path))
    root = tree.getroot()

    activity_el = _find(root, "Activities/Activity")
    if activity_el is None:
        return Activity(source_format=SOURCE_FORMAT, source_path=str(path))

    segments = [
        Segment(points=[_read_point(tp) for tp in _findall(track, "Trackpoint")])
        for track in _findall(activity_el, "Lap/Track")
    ]

    return Activity(
        sport=_sport_from_attribute(activity_el.get("Sport")),
        device=_read_device(activity_el),
        segments=segments,
        time_created=_read_id_time(activity_el),
        source_format=SOURCE_FORMAT,
        source_path=str(path),
    )


def _sport_from_attribute(sport: str | None) -> str | None:
    """Map a TCX ``Sport`` attribute to our canonical sport, or ``None``."""
    if sport is None:
        return None
    return _SPORT_FROM_TCX.get(sport, _DEFAULT_SPORT)


def _read_id_time(activity_el: etree._Element) -> datetime | None:
    """Return the activity's ``<Id>`` timestamp as UTC, or ``None`` if absent."""
    text = _text(_find(activity_el, "Id"))
    return parse_time(text) if text is not None else None


def _read_device(activity_el: etree._Element) -> Device:
    """Read the recording device from the activity's ``<Creator>``."""
    creator = _find(activity_el, "Creator")
    if creator is None:
        return Device()
    serial = _text(_find(creator, "UnitId"))
    return Device(
        name=_text(_find(creator, "Name")),
        product=_text(_find(creator, "ProductID")),
        serial_number=int(serial) if serial is not None else None,
    )


def _read_point(trackpoint: etree._Element) -> TrackPoint:
    """Convert one ``<Trackpoint>``, including its TPX extension, to a TrackPoint."""
    time_text = _text(_find(trackpoint, "Time"))
    hr_text = _text(_find(trackpoint, "HeartRateBpm/Value"))
    cad_text = _text(_find(trackpoint, "Cadence"))

    # The TPX extension carries power, speed, and (for runs) cadence. It lives in
    # its own namespace, so match its children by local name (any prefix), the
    # way gpx.py reads its extensions. Running cadence wins over the core
    # <Cadence> when both are present.
    power: int | None = None
    speed: float | None = None
    for child in _tpx_children(trackpoint):
        local = _local_name(child.tag)
        text = _text(child)
        if local == "Watts":
            power = _to_int(text, power)
        elif local == "Speed":
            speed = _to_float(text, speed)
        elif local == "RunCadence" and text is not None:
            cad_text = text

    return TrackPoint(
        time=parse_time(time_text) if time_text is not None else None,
        lat=_to_float(_text(_find(trackpoint, "Position/LatitudeDegrees"))),
        lon=_to_float(_text(_find(trackpoint, "Position/LongitudeDegrees"))),
        ele=_to_float(_text(_find(trackpoint, "AltitudeMeters"))),
        distance=_to_float(_text(_find(trackpoint, "DistanceMeters"))),
        hr=_to_int(hr_text),
        cad=_to_int(cad_text),
        power=power,
        speed=speed,
    )


def _tpx_children(trackpoint: etree._Element) -> list[etree._Element]:
    """Return the children of every ``<TPX>`` under the point's ``<Extensions>``.

    The TPX element sits in the ActivityExtension namespace, not the TCX one, so
    we reach it by local name rather than by the TCX-namespaced path helpers.
    """
    children: list[etree._Element] = []
    for extensions in _findall(trackpoint, "Extensions"):
        for element in extensions:
            if _local_name(element.tag) == "TPX":
                children.extend(element)
    return children


def _local_name(tag: str) -> str:
    """Strip any ``{namespace}`` prefix from an lxml tag, leaving the local name."""
    return tag.rpartition("}")[2]


def _find(parent: etree._Element, path: str) -> etree._Element | None:
    """Find the first descendant matching a ``/``-separated path of local names.

    Each step matches by the TCX namespace plus local name, so prefixes in the
    source file are irrelevant.
    """
    element: etree._Element | None = parent
    for local_name in path.split("/"):
        if element is None:
            return None
        element = element.find(_qualified(TCX_NS, local_name))
    return element


def _findall(parent: etree._Element, path: str) -> list[etree._Element]:
    """Find every descendant matching a ``/``-separated path of local names.

    Like :func:`_find` but returns all matches at the final step, expanding each
    intermediate match so multiple laps or tracks are all walked.
    """
    elements = [parent]
    for local_name in path.split("/"):
        tag = _qualified(TCX_NS, local_name)
        elements = [child for el in elements for child in el.findall(tag)]
    return elements


def _text(element: etree._Element | None) -> str | None:
    """Return an element's stripped text, or ``None`` if absent or empty."""
    if element is None or element.text is None:
        return None
    stripped = element.text.strip()
    return stripped or None


def _to_float(value: str | None, default: float | None = None) -> float | None:
    """Parse a float, or return `default` when `value` is ``None``."""
    return float(value) if value is not None else default


def _to_int(value: str | None, default: int | None = None) -> int | None:
    """Parse an int (via float, to tolerate ``"180.0"``), or return `default`."""
    return int(float(value)) if value is not None else default


def write_tcx(activity: Activity, path: Path) -> None:
    """Write an :class:`Activity` to a valid TCX file.

    Emits one ``<Activity>`` holding one ``<Lap>`` (we do not model laps yet) with
    one ``<Track>`` per segment, so pauses survive as separate tracks. Trackpoint
    children are written in TCX schema order, and only for channels that are not
    ``None`` -- a missing channel is omitted, never written as ``0``.

    Two kinds of data cannot survive a TCX round-trip and are intentionally not
    written: per-point **temperature** -- TCX has no temperature field anywhere in
    its schema, so ``temp`` is dropped and reads back as ``None``; and the
    structured :class:`Device` identity beyond name, product, and serial number
    (manufacturer and software version have no place in the TCX ``<Creator>``).

    Heart rate and cadence are written as integers, as the schema requires.

    Parameters
    ----------
    activity : Activity
        The activity to serialise.
    path : Path
        Destination file path; overwritten if it exists.
    """
    # ``None`` registers TCX as the default (unprefixed) namespace; the TPX prefix
    # carries the power/speed extension. lxml accepts a ``None`` key at runtime,
    # but its stub types the map as str->str, hence the targeted ignore.
    nsmap = {None: TCX_NS, "ns3": TPX_NS, "xsi": _XSI_NS}
    root = etree.Element(
        _qualified(TCX_NS, "TrainingCenterDatabase"),
        nsmap=nsmap,  # type: ignore[arg-type]
    )
    activities = etree.SubElement(root, _qualified(TCX_NS, "Activities"))
    activity_el = etree.SubElement(activities, _qualified(TCX_NS, "Activity"))
    activity_el.set("Sport", _sport_to_attribute(activity.sport))

    start = _activity_start(activity)
    etree.SubElement(activity_el, _qualified(TCX_NS, "Id")).text = _format_time(start)

    _write_lap(activity_el, activity, start)
    _write_creator(activity_el, activity.device)

    tree = etree.ElementTree(root)
    tree.write(str(path), xml_declaration=True, encoding="UTF-8", pretty_print=True)


def _sport_to_attribute(sport: str | None) -> str:
    """Map our canonical sport to a TCX ``Sport`` attribute, defaulting to Other."""
    if sport is None:
        return _TCX_OTHER_SPORT
    return _SPORT_TO_TCX.get(sport, _TCX_OTHER_SPORT)


def _activity_start(activity: Activity) -> datetime | None:
    """Return the first point's time, or the activity's creation time as a fallback."""
    for point in activity.points():
        if point.time is not None:
            return point.time
    return activity.time_created


def _write_lap(
    activity_el: etree._Element, activity: Activity, start: datetime | None
) -> None:
    """Write the single ``<Lap>`` with its summary fields then one track per segment.

    The lap's children must appear in schema order: total time, distance,
    calories, intensity, trigger method, then the tracks.
    """
    lap = etree.SubElement(activity_el, _qualified(TCX_NS, "Lap"))
    lap.set("StartTime", _format_time(start))

    _child(lap, "TotalTimeSeconds", _format_number(_total_seconds(activity)))
    _child(lap, "DistanceMeters", _format_number(_total_distance(activity)))
    _child(lap, "Calories", "0")
    _child(lap, "Intensity", "Active")
    _child(lap, "TriggerMethod", "Manual")

    for segment in activity.segments:
        track = etree.SubElement(lap, _qualified(TCX_NS, "Track"))
        for point in segment.points:
            _write_point(track, point)


def _total_seconds(activity: Activity) -> float:
    """Sum the elapsed seconds within each segment (pauses between are excluded)."""
    total = 0.0
    for segment in activity.segments:
        times = [p.time for p in segment.points if p.time is not None]
        if len(times) >= 2:
            total += (times[-1] - times[0]).total_seconds()
    return total


def _total_distance(activity: Activity) -> float:
    """Return the largest recorded cumulative distance, or ``0`` if none recorded."""
    distances = [p.distance for p in activity.points() if p.distance is not None]
    return max(distances) if distances else 0.0


def _write_point(track: etree._Element, point: TrackPoint) -> None:
    """Write one ``<Trackpoint>`` with children in strict TCX schema order."""
    trackpoint = etree.SubElement(track, _qualified(TCX_NS, "Trackpoint"))

    if point.time is not None:
        _child(trackpoint, "Time", _format_time(point.time))

    if point.lat is not None and point.lon is not None:
        position = etree.SubElement(trackpoint, _qualified(TCX_NS, "Position"))
        _child(position, "LatitudeDegrees", _format_number(point.lat))
        _child(position, "LongitudeDegrees", _format_number(point.lon))

    if point.ele is not None:
        _child(trackpoint, "AltitudeMeters", _format_number(point.ele))

    if point.distance is not None:
        _child(trackpoint, "DistanceMeters", _format_number(point.distance))

    if point.hr is not None:
        hr = etree.SubElement(trackpoint, _qualified(TCX_NS, "HeartRateBpm"))
        _child(hr, "Value", str(int(point.hr)))

    if point.cad is not None:
        _child(trackpoint, "Cadence", str(int(point.cad)))

    _write_tpx(trackpoint, point)


def _write_tpx(trackpoint: etree._Element, point: TrackPoint) -> None:
    """Write the TPX extension for speed and power, or nothing if both are absent."""
    if point.speed is None and point.power is None:
        return
    extensions = etree.SubElement(trackpoint, _qualified(TCX_NS, "Extensions"))
    tpx = etree.SubElement(extensions, _qualified(TPX_NS, "TPX"))
    if point.speed is not None:
        _child(tpx, "Speed", _format_number(point.speed), namespace=TPX_NS)
    if point.power is not None:
        _child(tpx, "Watts", str(int(point.power)), namespace=TPX_NS)


def _write_creator(activity_el: etree._Element, device: Device) -> None:
    """Write the ``<Creator>`` naming the recording device."""
    creator = etree.SubElement(activity_el, _qualified(TCX_NS, "Creator"))
    creator.set(_qualified(_XSI_NS, "type"), "Device_t")
    _child(creator, "Name", device.name or _DEFAULT_CREATOR)
    if device.serial_number is not None:
        _child(creator, "UnitId", str(device.serial_number))
    if device.product is not None:
        _child(creator, "ProductID", device.product)


def _child(
    parent: etree._Element, local_name: str, text: str, namespace: str = TCX_NS
) -> etree._Element:
    """Append a child element with the given text, in the TCX namespace by default."""
    element = etree.SubElement(parent, _qualified(namespace, local_name))
    element.text = text
    return element


def _format_time(value: datetime | None) -> str:
    """Render a UTC datetime as TCX's ISO-8601 with a trailing ``Z``.

    Falls back to the Unix epoch when `value` is ``None`` so the mandatory
    ``<Id>`` and lap ``StartTime`` always carry a valid timestamp.
    """
    if value is None:
        return _EPOCH_TIME
    return value.strftime(_TCX_TIME_FORMAT)


def _format_number(value: float) -> str:
    """Render a number: integers without a trailing ``.0``, floats as-is."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _qualified(namespace: str, local_name: str) -> str:
    """Return an lxml Clark-notation tag ``{namespace}local_name``."""
    return f"{{{namespace}}}{local_name}"
