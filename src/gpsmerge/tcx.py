"""Read and write TCX (Garmin Training Center XML) activity files via lxml.

TCX is Garmin's older XML format. Unlike GPX's bare list of points, TCX is built
around laps: an ``<Activity>`` holds one or more ``<Lap>`` elements, and the
trackpoints live inside a lap under a ``<Track>``. A file may also hold several
``<Activity>`` elements (multisport / Training Center), which we read in order. We
do not model laps yet, so on read we flatten every lap's tracks (across every
activity) into one segment list, and on write we emit a single lap whose tracks
are our segments. Each ``<Track>`` is one continuous run of recording, so the gap
between two tracks is a pause -- exactly our
:class:`~gpsmerge.model.Segment` boundary.

TCX is untrusted input, so it is parsed with a hardened lxml parser
(:data:`_PARSER`) that resolves no entities, touches no network, and loads no DTD,
closing off XXE and entity-expansion attacks.

Two namespaces carry the data:

- the TCX namespace itself holds position, time, altitude, distance, heart rate,
  and cadence directly in the schema (no extension hunting, unlike GPX).
- the Garmin ActivityExtension (TPX) namespace carries the channels TCX core
  lacks: ``Watts`` (power), ``Speed``, and ``RunCadence`` (running cadence).

On read we match elements by their namespace URI plus local name, never by prefix
string, so a file is understood whichever prefix its author chose.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from pathlib import Path

from lxml import etree

from gpsmerge.geo import cumulative_distance
from gpsmerge.model import Activity, Device, Lap, Segment, TrackPoint
from gpsmerge.timeutil import parse_time

# A hardened parser for untrusted input. lxml's default parser resolves entities
# and can be steered into reading local files (XXE) or expanding a nested-entity
# "billion laughs" bomb. We turn all three off: do not resolve entities, never
# touch the network, and do not load the DTD where those entities are defined.
_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)

# Garmin namespaces, written exactly as Garmin Training Center produces them.
# IMPORTANT: these are XML *namespace identifiers*, not URLs to fetch -- a browser
# 404 on them is expected and irrelevant. They must match these exact strings or
# real TCX files stop being recognised. (The downloadable schema lives separately
# at https://www8.garmin.com/xmlschemas/TrainingCenterDatabasev2.xsd, which is a
# schema-location, not the namespace.) test_namespaces.py pins these.
TCX_NS = "http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2"
TPX_NS = "http://www.garmin.com/xmlschemas/ActivityExtension/v2"
_XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"

# Downloadable schema documents (NOT namespaces) for xsi:schemaLocation on the
# root, matching what real Garmin output carries. Garmin's own files point at a
# www.garmin.com host that no longer serves the .xsd; we point at the resolvable
# www8 mirror instead, so a validator following the link actually gets the schema.
# Both were verified to return HTTP 200. Each is paired with its namespace below.
_TCX_SCHEMA_URL = "https://www8.garmin.com/xmlschemas/TrainingCenterDatabasev2.xsd"
_TPX_SCHEMA_URL = "https://www8.garmin.com/xmlschemas/ActivityExtensionv2.xsd"

# xsi:schemaLocation is a space-separated list of (namespace, schema-URL) pairs.
_SCHEMA_LOCATION = f"{TCX_NS} {_TCX_SCHEMA_URL} {TPX_NS} {_TPX_SCHEMA_URL}"

# Map the TCX Sport attribute to our canonical lowercase sport, and back. Any
# Sport we do not recognise reads as "other"; any sport we cannot name writes as
# "Other", TCX's catch-all.
_SPORT_FROM_TCX = {"Biking": "cycling", "Running": "running"}
_SPORT_TO_TCX = {"cycling": "Biking", "running": "Running"}
_DEFAULT_SPORT = "other"
_TCX_OTHER_SPORT = "Other"

# Map the TCX <TriggerMethod> to our canonical lap trigger, and back. TCX has no
# "session_end" trigger, so we never produce it on read; on write any trigger we
# do not have a TCX name for (including "session_end") falls back to "Manual",
# TCX's safe default. "Location" is TCX's name for our "position" trigger.
_TRIGGER_FROM_TCX = {
    "Manual": "manual",
    "Distance": "distance",
    "Time": "time",
    "Location": "position",
    "HeartRate": "heart_rate",
}
_TRIGGER_TO_TCX = {
    "manual": "Manual",
    "distance": "Distance",
    "time": "Time",
    "position": "Location",
    "heart_rate": "HeartRate",
}
_DEFAULT_TRIGGER_TCX = "Manual"

# Used for the <Creator><Name> when an activity has no device name.
_DEFAULT_CREATOR = "gpsmerge"

# ISO-8601 in UTC with a trailing Z, the form TCX timestamps take. Time is the
# merge/overlap key, so we must not collapse sub-second points: we emit whole
# seconds when a point lands on one, and milliseconds when it carries a fraction.
_TCX_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_TCX_TIME_FORMAT_SUBSECOND = "%Y-%m-%dT%H:%M:%S.%fZ"
_EPOCH_TIME = "1970-01-01T00:00:00Z"
# strftime("%f") gives microseconds (6 digits); TCX timestamps use milliseconds,
# so we keep the first three digits of the fractional second.
_MILLISECONDS_DIGITS = 3

SOURCE_FORMAT = "tcx"


def read_tcx(path: Path) -> Activity:
    """Parse a TCX file into the canonical :class:`Activity`.

    Every ``<Activity>`` is read -- multisport and Training Center files can hold
    several. Each one's ``<Track>`` elements (across all its laps, in order)
    become :class:`Segment` objects, and they are concatenated in document order.
    Each ``<Lap>`` becomes a boundaries-only :class:`Lap` (start time, end time
    from ``TotalTimeSeconds`` when present, and the mapped trigger); laps are
    concatenated across every activity too, and are kept independent of the
    segment (pause) boundaries. The sport, device, and creation time are taken
    from the first ``<Activity>``. Every ``<Trackpoint>`` becomes a
    :class:`TrackPoint`; power, speed, and running cadence are read from the
    Garmin ActivityExtension.

    The file is parsed with a hardened parser (no entity resolution, no network,
    no DTD loading) because TCX is untrusted input.

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
    tree = etree.parse(str(path), parser=_PARSER)
    root = tree.getroot()

    activities = _findall(root, "Activities/Activity")
    if not activities:
        return Activity(source_format=SOURCE_FORMAT, source_path=str(path))

    segments = [
        Segment(points=[_read_point(tp) for tp in _findall(track, "Trackpoint")])
        for activity_el in activities
        for track in _findall(activity_el, "Lap/Track")
    ]
    laps = [
        _read_lap(lap_el)
        for activity_el in activities
        for lap_el in _findall(activity_el, "Lap")
    ]

    first = activities[0]
    return Activity(
        sport=_sport_from_attribute(first.get("Sport")),
        device=_read_device(first),
        segments=segments,
        laps=laps,
        time_created=_read_id_time(first),
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


def _read_lap(lap_el: etree._Element) -> Lap:
    """Build a boundaries-only :class:`Lap` from one ``<Lap>`` element.

    The ``StartTime`` attribute gives the start; ``end_time`` is the start plus
    ``TotalTimeSeconds`` when that child is present, else ``None``. The trigger is
    the mapped ``<TriggerMethod>``, or ``None`` when absent or unrecognised. Only
    the span and trigger are kept -- the lap's own summary totals are recomputed
    from its points on write, so we deliberately ignore them here.
    """
    # StartTime is mandatory in the TCX schema; fall back to the epoch only so a
    # malformed lap without it still parses rather than crashing the whole read.
    start = parse_time(lap_el.get("StartTime") or _EPOCH_TIME)
    total_seconds = _to_float(_text(_find(lap_el, "TotalTimeSeconds")))
    end_time = (
        start + timedelta(seconds=total_seconds) if total_seconds is not None else None
    )
    trigger_text = _text(_find(lap_el, "TriggerMethod"))
    trigger = _TRIGGER_FROM_TCX.get(trigger_text) if trigger_text is not None else None
    return Lap(start_time=start, end_time=end_time, trigger=trigger)


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

    Emits one ``<Activity>`` whose laps depend on the model. When the activity
    has no laps, one synthetic ``<Lap>`` spans the whole activity with one
    ``<Track>`` per segment, so pauses survive as separate tracks. When the
    activity has laps, one ``<Lap>`` is emitted per lap in start-time order, and
    every point is assigned to a lap (partitioned by lap start time) so no point
    is lost; within a lap the points still split into a ``<Track>`` at each
    segment boundary, so a pause inside a lap survives as separate tracks. Each
    lap's summary totals (time, distance, trigger) are recomputed from its own
    points rather than trusting any stored value, so they cannot go stale.

    Trackpoint children are written in TCX schema order, and only for channels
    that are not ``None`` -- a missing channel is omitted, never written as
    ``0``. A non-finite value (NaN or infinity) is treated as absent for the same
    reason. A point with only one of latitude/longitude still gets a
    ``<Position>`` carrying the coordinate it has, so a lone coordinate is not
    dropped. Times that carry a fractional second are written with millisecond
    precision so sub-second points survive.

    Two kinds of data cannot survive a TCX round-trip and are intentionally not
    written: per-point **temperature** -- TCX has no temperature field anywhere in
    its schema, so ``temp`` is dropped and reads back as ``None``; and the
    structured :class:`Device` identity beyond name, product, and serial number
    (manufacturer and software version have no place in the TCX ``<Creator>``).
    Within laps, only the **span and trigger** round-trip: a lap's ``<Calories>``
    and ``<Intensity>`` are written as fixed placeholders and are not retained on
    read.

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
    # Advertise the schemas the way Garmin's own output does, so validators can
    # find them. The xsi prefix is already declared via nsmap above.
    root.set(_qualified(_XSI_NS, "schemaLocation"), _SCHEMA_LOCATION)
    activities = etree.SubElement(root, _qualified(TCX_NS, "Activities"))
    activity_el = etree.SubElement(activities, _qualified(TCX_NS, "Activity"))
    activity_el.set("Sport", _sport_to_attribute(activity.sport))

    start = _activity_start(activity)
    etree.SubElement(activity_el, _qualified(TCX_NS, "Id")).text = _format_time(start)

    if activity.laps:
        _write_laps(activity_el, activity)
    else:
        _write_synthetic_lap(activity_el, activity, start)
    _write_creator(activity_el, activity.device)

    tree = etree.ElementTree(root)
    tree.write(str(path), xml_declaration=True, encoding="UTF-8", pretty_print=True)


def _sport_to_attribute(sport: str | None) -> str:
    """Map our canonical sport to a TCX ``Sport`` attribute, defaulting to Other."""
    if sport is None:
        return _TCX_OTHER_SPORT
    return _SPORT_TO_TCX.get(sport, _TCX_OTHER_SPORT)


def _trigger_to_tcx(trigger: str | None) -> str:
    """Map our lap trigger to a TCX ``<TriggerMethod>``, defaulting to Manual.

    TCX has no name for ``"session_end"`` (or any trigger we do not recognise),
    so it falls back to ``"Manual"`` -- the schema's safe default.
    """
    if trigger is None:
        return _DEFAULT_TRIGGER_TCX
    return _TRIGGER_TO_TCX.get(trigger, _DEFAULT_TRIGGER_TCX)


def _activity_start(activity: Activity) -> datetime | None:
    """Return the first point's time, or the activity's creation time as a fallback."""
    for point in activity.points():
        if point.time is not None:
            return point.time
    return activity.time_created


def _write_synthetic_lap(
    activity_el: etree._Element, activity: Activity, start: datetime | None
) -> None:
    """Write a single ``<Lap>`` spanning the activity, one ``<Track>`` per segment.

    Used when the activity carries no laps of its own (e.g. it came from GPX).
    The lap's children must appear in schema order: total time, distance,
    calories, intensity, trigger method, then the tracks.
    """
    lap = etree.SubElement(activity_el, _qualified(TCX_NS, "Lap"))
    lap.set("StartTime", _format_time(start))

    _child(lap, "TotalTimeSeconds", _format_number(_total_seconds(activity)))
    _child(lap, "DistanceMeters", _format_number(_total_distance(activity)))
    _child(lap, "Calories", "0")
    _child(lap, "Intensity", "Active")
    _child(lap, "TriggerMethod", _DEFAULT_TRIGGER_TCX)

    for segment in activity.segments:
        track = etree.SubElement(lap, _qualified(TCX_NS, "Track"))
        for point in segment.points:
            _write_point(track, point)


def _write_laps(activity_el: etree._Element, activity: Activity) -> None:
    """Write one ``<Lap>`` per model lap, assigning every point to exactly one.

    Points are partitioned by lap start time so the assignment is contiguous and
    never loses a point: a point belongs to the latest lap whose ``start_time``
    is at or before the point's time, and any point before the first lap (or with
    no time) joins the first lap. Within a lap the points are split into a
    ``<Track>`` at each segment boundary, so a pause inside a lap survives as
    separate tracks. A lap that ends up with no points is skipped.
    """
    laps = sorted(activity.laps, key=lambda lap: lap.start_time)
    segment_index = _segment_index_by_point(activity)
    points_by_lap = _partition_points_by_lap(activity, laps)

    for lap, lap_points in zip(laps, points_by_lap):
        if not lap_points:
            continue
        _write_lap(activity_el, lap, lap_points, segment_index)


def _write_lap(
    activity_el: etree._Element,
    lap: Lap,
    points: list[TrackPoint],
    segment_index: dict[int, int],
) -> None:
    """Write one ``<Lap>`` with summaries recomputed from its own `points`.

    Children appear in strict TCX schema order: total time, distance, calories,
    intensity, trigger method, then the tracks. `points` is non-empty (empty laps
    are skipped by the caller). `segment_index` maps a point's ``id`` to the
    index of its source segment, so the points split into a new ``<Track>``
    wherever that index changes -- preserving pauses inside the lap.
    """
    lap_el = etree.SubElement(activity_el, _qualified(TCX_NS, "Lap"))
    lap_el.set("StartTime", _format_time(lap.start_time))

    _child(lap_el, "TotalTimeSeconds", _format_number(_lap_seconds(points)))
    _child(lap_el, "DistanceMeters", _format_number(_lap_distance(points)))
    _child(lap_el, "Calories", "0")
    _child(lap_el, "Intensity", "Active")
    _child(lap_el, "TriggerMethod", _trigger_to_tcx(lap.trigger))

    _write_tracks(lap_el, points, segment_index)


def _write_tracks(
    lap_el: etree._Element, points: list[TrackPoint], segment_index: dict[int, int]
) -> None:
    """Split `points` into ``<Track>`` elements at every segment boundary.

    A new ``<Track>`` opens whenever a point's source-segment index differs from
    the previous point's, so a pause that fell inside this lap survives as two
    tracks. Points carry their original recorded order.
    """
    track: etree._Element | None = None
    previous_segment: int | None = None
    for point in points:
        current_segment = segment_index.get(id(point))
        if track is None or current_segment != previous_segment:
            track = etree.SubElement(lap_el, _qualified(TCX_NS, "Track"))
            previous_segment = current_segment
        _write_point(track, point)


def _segment_index_by_point(activity: Activity) -> dict[int, int]:
    """Map each point's ``id`` to the index of the segment that holds it.

    Built once by walking the segments so the writer can tell, for any point,
    which segment (pause boundary) it came from. ``id`` is used as the key
    because two distinct points can compare equal yet still belong to different
    segments.
    """
    return {
        id(point): index
        for index, segment in enumerate(activity.segments)
        for point in segment.points
    }


def _partition_points_by_lap(
    activity: Activity, laps: list[Lap]
) -> list[list[TrackPoint]]:
    """Assign every activity point to exactly one lap, by lap start time.

    `laps` must already be sorted by ``start_time``. A point joins the latest lap
    whose ``start_time`` is at or before the point's time; a point that predates
    the first lap, or that carries no time, joins the first lap. The result has
    one list per lap, in the same order as `laps`, and together the lists hold
    every point exactly once.
    """
    buckets: list[list[TrackPoint]] = [[] for _ in laps]
    starts = [lap.start_time for lap in laps]
    for point in activity.points():
        buckets[_lap_index_for_point(point, starts)].append(point)
    return buckets


def _lap_index_for_point(point: TrackPoint, starts: list[datetime]) -> int:
    """Return the index of the lap a point belongs to, by start-time partitioning.

    `starts` is the laps' start times in ascending order. The point belongs to
    the latest lap that started at or before it; a point before the first lap (or
    with no time) belongs to the first lap (index ``0``).
    """
    if point.time is None:
        return 0
    chosen = 0
    for index, start in enumerate(starts):
        if start <= point.time:
            chosen = index
        else:
            break
    return chosen


def _lap_seconds(points: list[TrackPoint]) -> float:
    """Return the elapsed seconds across a lap's points (last time minus first)."""
    times = [p.time for p in points if p.time is not None]
    if len(times) < 2:
        return 0.0
    return (times[-1] - times[0]).total_seconds()


def _lap_distance(points: list[TrackPoint]) -> float:
    """Return a lap's distance: recorded span if available, else haversine length.

    When the lap's first and last points both carry a cumulative ``distance``,
    their difference is the lap's distance. Otherwise the haversine length over
    the lap's positioned points is used, and ``0`` when neither is available.
    Non-finite recorded distances are ignored so a stray NaN/inf cannot leak in.
    """
    first = _finite(points[0].distance)
    last = _finite(points[-1].distance)
    if first is not None and last is not None:
        return last - first
    measured = cumulative_distance(points)
    return measured[-1] if measured else 0.0


def _total_seconds(activity: Activity) -> float:
    """Sum the elapsed seconds within each segment (pauses between are excluded)."""
    total = 0.0
    for segment in activity.segments:
        times = [p.time for p in segment.points if p.time is not None]
        if len(times) >= 2:
            total += (times[-1] - times[0]).total_seconds()
    return total


def _total_distance(activity: Activity) -> float:
    """Return the largest recorded cumulative distance, or ``0`` if none recorded.

    Non-finite distances are ignored so a stray NaN/inf cannot leak into the
    schema-invalid lap ``DistanceMeters`` summary.
    """
    distances = [
        finite for p in activity.points() if (finite := _finite(p.distance)) is not None
    ]
    return max(distances) if distances else 0.0


def _write_point(track: etree._Element, point: TrackPoint) -> None:
    """Write one ``<Trackpoint>`` with children in strict TCX schema order."""
    trackpoint = etree.SubElement(track, _qualified(TCX_NS, "Trackpoint"))

    if point.time is not None:
        _child(trackpoint, "Time", _format_time(point.time))

    _write_position(trackpoint, point)

    ele = _finite(point.ele)
    if ele is not None:
        _child(trackpoint, "AltitudeMeters", _format_number(ele))

    distance = _finite(point.distance)
    if distance is not None:
        _child(trackpoint, "DistanceMeters", _format_number(distance))

    if point.hr is not None:
        hr = etree.SubElement(trackpoint, _qualified(TCX_NS, "HeartRateBpm"))
        _child(hr, "Value", str(int(point.hr)))

    if point.cad is not None:
        _child(trackpoint, "Cadence", str(int(point.cad)))

    _write_tpx(trackpoint, point)


def _write_position(trackpoint: etree._Element, point: TrackPoint) -> None:
    """Write the ``<Position>`` when either coordinate is present, omitting absentees.

    The reader keeps a lone latitude or longitude, so requiring both here would
    silently drop a recorded coordinate on a TCX round-trip. We emit ``<Position>``
    whenever either finite coordinate exists and write only the children we have.
    """
    lat = _finite(point.lat)
    lon = _finite(point.lon)
    if lat is None and lon is None:
        return
    position = etree.SubElement(trackpoint, _qualified(TCX_NS, "Position"))
    if lat is not None:
        _child(position, "LatitudeDegrees", _format_number(lat))
    if lon is not None:
        _child(position, "LongitudeDegrees", _format_number(lon))


def _write_tpx(trackpoint: etree._Element, point: TrackPoint) -> None:
    """Write the TPX extension for speed and power, or nothing if both are absent."""
    speed = _finite(point.speed)
    if speed is None and point.power is None:
        return
    extensions = etree.SubElement(trackpoint, _qualified(TCX_NS, "Extensions"))
    tpx = etree.SubElement(extensions, _qualified(TPX_NS, "TPX"))
    if speed is not None:
        _child(tpx, "Speed", _format_number(speed), namespace=TPX_NS)
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

    A point that carries microseconds is written with millisecond precision
    (``...:SS.mmmZ``) because time is the merge key and sub-second points must
    not collapse; a whole-second point is written without a fractional part.
    Falls back to the Unix epoch when `value` is ``None`` so the mandatory
    ``<Id>`` and lap ``StartTime`` always carry a valid timestamp.
    """
    if value is None:
        return _EPOCH_TIME
    if value.microsecond == 0:
        return value.strftime(_TCX_TIME_FORMAT)
    # strftime gives 6-digit microseconds; trim to milliseconds and re-add the Z.
    formatted = value.strftime(_TCX_TIME_FORMAT_SUBSECOND)
    fraction_start = formatted.index(".") + 1
    millis = formatted[fraction_start : fraction_start + _MILLISECONDS_DIGITS]
    return f"{formatted[: fraction_start - 1]}.{millis}Z"


def _finite(value: float | None) -> float | None:
    """Return `value` if it is a finite number, else ``None``.

    A NaN or infinity would serialise to the literal ``"nan"``/``"inf"``, which
    is invalid against the TCX schema, so we treat any non-finite value as an
    absent channel -- the same as ``None``.
    """
    if value is None or not math.isfinite(value):
        return None
    return value


def _format_number(value: float) -> str:
    """Render a number: integers without a trailing ``.0``, floats as-is."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _qualified(namespace: str, local_name: str) -> str:
    """Return an lxml Clark-notation tag ``{namespace}local_name``."""
    return f"{{{namespace}}}{local_name}"
