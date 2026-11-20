"""Read FIT (Garmin/ANT binary) activity files via fitdecode.

FIT is the compact binary format head units actually record to. fitdecode does
the heavy lifting: it parses the typed message stream and decodes each field
for us, so timestamps come back as timezone-aware UTC datetimes, manufacturer and
product come back as readable names, and scaled sensor channels (altitude in
metres, speed in m/s) arrive already in their real units. Position is the
exception: it is semicircles and we convert it (see below). This module is the
small glue that maps those decoded messages onto the canonical :class:`Activity`.

Two FIT specifics are handled here rather than left to the model:

- **Position is in semicircles, not degrees.** The ``record`` message stores
  latitude and longitude as signed 32-bit integers; we convert them with
  :func:`gpsmerge.util.geo.semicircles_to_degrees`.
- **Enhanced channels win.** FIT carries both ``altitude``/``speed`` and the
  wider-range ``enhanced_altitude``/``enhanced_speed``. When an enhanced value
  is present we prefer it, because it is the one a modern device records when the
  plain field would clip.

Pauses survive as segment boundaries: a ``timer`` ``stop`` event closes the
current segment and the next ``timer`` ``start`` opens a fresh one, so a coffee
stop reads back as two segments rather than one continuous run.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import fitdecode

from gpsmerge.errors import WriteError
from gpsmerge.model import Activity, Device, Lap, Segment, TrackPoint
from gpsmerge.util.geo import semicircles_to_degrees

SOURCE_FORMAT = "fit"

# FIT event messages name the subsystem in ``event`` and what it did in
# ``event_type``. A timer stop (or stop_all) marks a pause; the next timer start
# resumes recording. These are the boundaries between our segments.
_TIMER_EVENT = "timer"
_STOP_EVENT_TYPES = frozenset({"stop", "stop_all"})

# Map the FIT ``lap_trigger`` enum (decoded to a lowercase string by fitdecode)
# to our canonical trigger set. FIT distinguishes several position triggers
# (``position_start``, ``position_lap``, ...); we collapse the whole family to
# ``"position"`` because the model only records that a lap was position-based.
# Anything else (e.g. ``fitness_equipment``) is an unmodelled trigger and reads
# as ``None``.
_LAP_TRIGGERS = {
    "manual": "manual",
    "distance": "distance",
    "time": "time",
    "session_end": "session_end",
}
_POSITION_TRIGGER_PREFIX = "position"
_POSITION_TRIGGER = "position"

# Map the FIT ``sport`` enum (already decoded to a lowercase string by fitdecode)
# to our canonical sport. The values we care about already match, but going via
# an explicit map keeps the canonical set in one place and ignores FIT sports we
# do not model.
_CANONICAL_SPORTS = frozenset({"cycling", "running", "swimming", "walking", "hiking"})

# --- write side --------------------------------------------------------------

# Our canonical sports, paired with the FIT ``Sport`` enum *member name* to write
# them as. (We name the member rather than import the enum at module top, so the
# read path keeps working with only fitdecode; fit-tool is an optional, lazily
# imported dependency.) A sport not in this map has no FIT enum we trust, so we
# omit the sport message rather than guess; it reads back as ``None``.
_SPORT_TO_FIT = {
    "cycling": "CYCLING",
    "running": "RUNNING",
    "swimming": "SWIMMING",
    "walking": "WALKING",
    "hiking": "HIKING",
}

# Our canonical lap triggers, paired with the FIT ``LapTrigger`` enum member name
# to write them as. A trigger we cannot name falls back to ``MANUAL``, the FIT
# safe default, mirroring how the TCX writer falls back to ``Manual``.
_TRIGGER_TO_FIT = {
    "manual": "MANUAL",
    "distance": "DISTANCE",
    "time": "TIME",
    "position": "POSITION_START",
    "session_end": "SESSION_END",
}
_DEFAULT_TRIGGER_FIT = "MANUAL"

# The FIT ``EventType`` member names for a timer start and a (full) stop. Named
# here so the timer-event helper resolves them by name, the same by-name pattern
# the sport/trigger/manufacturer maps use, keeping the enum import lazy.
_EVENT_TYPE_START = "START"
_EVENT_TYPE_STOP = "STOP_ALL"

# FIT timestamps are set on fit-tool messages in milliseconds since the Unix
# epoch (fit-tool converts to the FIT epoch itself). One whole second is 1000 ms.
_MILLISECONDS_PER_SECOND = 1000

# Optional-dependency guidance shown when fit-tool is not installed.
_FIT_TOOL_MISSING = (
    "writing FIT needs the optional 'fit-tool' dependency, which is not "
    "installed. Install it with: pip install 'gpsmerge[fit]' "
    "(or: uv sync --extra fit)"
)


def read_fit(path: Path) -> Activity:
    """Parse a FIT file into the canonical :class:`Activity`.

    Each ``record`` message becomes a :class:`TrackPoint`; ``file_id`` supplies
    the device identity and creation time; ``sport`` (or ``session``) supplies
    the sport. Records are grouped into :class:`Segment` runs split at each
    ``timer`` ``stop`` event, so recording pauses survive as segment boundaries.
    Each ``lap`` message becomes a :class:`Lap` overlay (span plus normalised
    trigger), kept independent of those segment boundaries.

    Parameters
    ----------
    path : Path
        Path to a FIT file.

    Returns
    -------
    Activity
        The parsed activity, with ``source_format="fit"`` and ``source_path``
        set to ``str(path)``. Position is converted from semicircles to degrees,
        ``enhanced_altitude``/``enhanced_speed`` are preferred over their plain
        counterparts, absent channels are ``None`` (never ``0``), and every
        timestamp is timezone-aware UTC.
    """
    device = Device()
    sport: str | None = None
    time_created: datetime | None = None

    # One open segment we append points to; closed (and a new one started) on a
    # timer stop so pauses become boundaries. Empty segments are never emitted.
    segments: list[Segment] = []
    current = Segment()

    # Laps are collected separately from segments: a ``lap`` message carries its
    # own span, independent of where recording paused.
    laps: list[Lap] = []

    with fitdecode.FitReader(path) as reader:
        for frame in reader:
            if not isinstance(frame, fitdecode.FitDataMessage):
                continue
            if frame.name == "record":
                current.points.append(_read_point(frame))
            elif frame.name == "lap":
                lap = _read_lap(frame)
                if lap is not None:
                    laps.append(lap)
            elif frame.name == "file_id":
                device = _read_device(frame)
                time_created = _get_time(frame, "time_created")
            elif frame.name in ("sport", "session") and sport is None:
                sport = _canonical_sport(_get_str(frame, "sport"))
            elif frame.name == "event" and _is_timer_stop(frame):
                current = _close_segment(segments, current)

    if current.points:
        segments.append(current)

    # Laps are appended in file order, which is normally already chronological;
    # sort defensively so a file with out-of-order lap messages still yields a
    # timeline-ordered overlay. A lap with no start_time cannot be placed and is
    # dropped by _read_lap, so every lap here has a start_time to sort on.
    laps.sort(key=lambda lap: lap.start_time)

    return Activity(
        sport=sport,
        device=device,
        segments=segments,
        laps=laps,
        time_created=_to_utc(time_created),
        source_format=SOURCE_FORMAT,
        source_path=str(path),
    )


def _read_point(frame: fitdecode.FitDataMessage) -> TrackPoint:
    """Convert one ``record`` message to a TrackPoint, in real units.

    Every field is coerced to the model's declared type at this boundary (via the
    ``_get_int``/``_get_float`` helpers) so no unchecked ``Any`` from fitdecode
    flows into the typed :class:`TrackPoint`.
    """
    return TrackPoint(
        time=_to_utc(_get_time(frame, "timestamp")),
        lat=_position_degrees(_get_int(frame, "position_lat")),
        lon=_position_degrees(_get_int(frame, "position_long")),
        ele=_prefer(
            _get_float(frame, "enhanced_altitude"), _get_float(frame, "altitude")
        ),
        distance=_get_float(frame, "distance"),
        hr=_get_int(frame, "heart_rate"),
        cad=_get_int(frame, "cadence"),
        power=_get_int(frame, "power"),
        temp=_get_float(frame, "temperature"),
        speed=_prefer(_get_float(frame, "enhanced_speed"), _get_float(frame, "speed")),
    )


def _read_lap(frame: fitdecode.FitDataMessage) -> Lap | None:
    """Convert one ``lap`` message to a :class:`Lap`, or ``None`` if unplaceable.

    A FIT ``lap`` records the span as ``start_time`` (lap start) and ``timestamp``
    (lap end), both decoded to tz-aware UTC, plus ``lap_trigger`` (why the lap
    ended). We store only that span and the normalised trigger; per-lap totals are
    recomputed from the points on write. A lap with no ``start_time`` cannot be
    positioned on the timeline, so it is dropped (``None``) rather than guessed at.
    """
    start_time = _to_utc(_get_time(frame, "start_time"))
    if start_time is None:
        return None
    return Lap(
        start_time=start_time,
        end_time=_to_utc(_get_time(frame, "timestamp")),
        trigger=_normalise_lap_trigger(_get_str(frame, "lap_trigger")),
    )


def _normalise_lap_trigger(trigger: str | None) -> str | None:
    """Map a decoded FIT ``lap_trigger`` to our canonical set, or ``None``.

    The several FIT position triggers (``position_start``, ``position_lap``, ...)
    all collapse to ``"position"``; ``manual``/``distance``/``time``/
    ``session_end`` map straight through; anything else is unmodelled and reads as
    ``None``.
    """
    if trigger is None:
        return None
    if trigger.startswith(_POSITION_TRIGGER_PREFIX):
        return _POSITION_TRIGGER
    return _LAP_TRIGGERS.get(trigger)


def _read_device(frame: fitdecode.FitDataMessage) -> Device:
    """Read the recording device from a ``file_id`` message.

    fitdecode decodes manufacturer and product to readable strings (e.g.
    ``"garmin"`` and ``"edge_530"``). For Garmin devices the product lives under
    ``garmin_product`` rather than the generic ``product`` field, so both are
    checked. The human ``name`` joins the two when both are known.
    """
    manufacturer = _get_str(frame, "manufacturer")
    product = _get_str(frame, "product") or _get_str(frame, "garmin_product")
    return Device(
        name=_device_name(manufacturer, product),
        manufacturer=manufacturer,
        product=product,
        serial_number=_get_int(frame, "serial_number"),
    )


def _device_name(manufacturer: str | None, product: str | None) -> str | None:
    """Build a readable device name from the FIT manufacturer and product.

    FIT gives enum-style strings like ``"stages_cycling"`` and (for mapped
    products) ``"edge_530"``, which we tidy to ``"Stages Cycling"`` and
    ``"Edge 530"``. An unmapped product comes back as a bare number (e.g. ``"1"``)
    that means nothing to a person, so it is dropped rather than appended (the
    raw value is still kept in :attr:`Device.product`).
    """
    maker = _prettify(manufacturer)
    model = _prettify(product) if product and not product.isdigit() else None
    if maker and model:
        return f"{maker} {model}"
    return maker or model


def _prettify(token: str | None) -> str | None:
    """Turn a FIT enum token like ``"stages_cycling"`` into ``"Stages Cycling"``."""
    if not token:
        return None
    words = token.replace("_", " ").split()
    return " ".join(word[:1].upper() + word[1:] for word in words)


def _is_timer_stop(frame: fitdecode.FitDataMessage) -> bool:
    """Return ``True`` if this event message is a timer stop (a recording pause)."""
    return (
        _get_str(frame, "event") == _TIMER_EVENT
        and _get_str(frame, "event_type") in _STOP_EVENT_TYPES
    )


def _close_segment(segments: list[Segment], current: Segment) -> Segment:
    """Emit `current` if it has points and return a fresh empty segment.

    A timer stop with no points since the last boundary (e.g. two stops in a row)
    adds no empty segment, so spurious boundaries do not create empty runs.
    """
    if current.points:
        segments.append(current)
    return Segment()


def _canonical_sport(sport: str | None) -> str | None:
    """Map a decoded FIT sport to our canonical sport, or ``None`` if unknown."""
    if sport is None:
        return None
    name = sport.lower()
    return name if name in _CANONICAL_SPORTS else None


def _position_degrees(semicircles: int | None) -> float | None:
    """Convert a semicircle position to degrees, or ``None`` if absent."""
    if semicircles is None:
        return None
    return semicircles_to_degrees(semicircles)


def _prefer(enhanced: float | None, plain: float | None) -> float | None:
    """Return `enhanced` when present, else `plain` (which may itself be ``None``)."""
    return enhanced if enhanced is not None else plain


class _Decoded(Protocol):
    """The slice of ``fitdecode.FitDataMessage`` this module reads.

    fitdecode ships no type stubs, so ``get_value`` is untyped ``Any``. Pinning
    the one method we call to this Protocol lets the coercion helpers below take a
    typed argument, so mypy checks the boundary where raw FIT values enter the
    typed model instead of waving the whole frame through as ``Any``.
    """

    def get_value(self, field_name_or_num: str, *, fallback: Any = ...) -> Any:
        """Return the decoded field value, or ``fallback`` if the field is absent."""


def _get(frame: _Decoded, field: str) -> Any:
    """Return a decoded field's value, or ``None`` when the record lacks it.

    fitdecode raises if you ask for a field a message does not carry, so this
    wraps ``get_value`` with a ``None`` fallback, making an absent channel read
    as ``None`` rather than an error. The typed ``_get_int``/``_get_float``/
    ``_get_str`` wrappers are what callers use; this stays the single place that
    touches the raw ``Any``.
    """
    return frame.get_value(field, fallback=None)


def _get_int(frame: _Decoded, field: str) -> int | None:
    """Return a field coerced to ``int``, or ``None`` when absent or non-numeric.

    Numeric FIT channels decode to ``int``/``float``; a ``bool`` is rejected
    because ``isinstance(True, int)`` is true yet a boolean is never a real count.
    """
    value = _get(frame, field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _get_float(frame: _Decoded, field: str) -> float | None:
    """Return a field coerced to ``float``, or ``None`` when absent or non-numeric."""
    value = _get(frame, field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _get_str(frame: _Decoded, field: str) -> str | None:
    """Return a field as a non-empty stripped string, or ``None`` when absent.

    FIT enums decode to readable strings; anything else is stringified so an
    unexpected type still crosses the boundary as a ``str`` rather than ``Any``.
    """
    value = _get(frame, field)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _get_time(frame: _Decoded, field: str) -> datetime | None:
    """Return a field as a ``datetime``, or ``None`` when absent or not a datetime.

    fitdecode decodes FIT timestamps to ``datetime`` objects; anything else is
    treated as absent so only a real datetime reaches :func:`_to_utc`.
    """
    value = _get(frame, field)
    return value if isinstance(value, datetime) else None


def _to_utc(value: datetime | None) -> datetime | None:
    """Return `value` as timezone-aware UTC, or ``None`` if it is ``None``.

    fitdecode already decodes FIT timestamps to UTC datetimes, but a naive value
    (or one in another zone) is normalised here so the model's UTC rule holds.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def write_fit(activity: Activity, path: Path) -> None:
    """Write an :class:`Activity` to a structurally complete FIT file.

    **Experimental.** FIT writing is the newest output and the binary format is
    unforgiving; the result is round-trip tested (see ``test_fit_write.py``) but
    has not been proven against every device's importer. The reader is the mature
    path, so treat a written FIT file as best-effort.

    The message stream mirrors what :func:`read_fit` expects, so a written file
    reads back faithfully: a ``file_id`` (device attribution), an optional
    ``sport`` message, a timer ``start`` event, one ``record`` per point, a timer
    ``stop`` between segments (so pauses survive as segment boundaries) and a
    final stop, one ``lap`` per model lap with its span and trigger, then a single
    ``session`` and ``activity`` summary so a real device or Strava accepts it.
    Per-lap and per-session totals are recomputed from the points on write, never
    trusted from a stored value, so they cannot go stale after a merge.

    A channel that is ``None`` is simply not set on the message, so fit-tool
    writes the FIT *invalid* sentinel and :func:`read_fit` maps it back to
    ``None``, so an absent channel never serialises as ``0``. Position is set in
    degrees (fit-tool encodes the semicircles itself).

    Known FIT-write losses, all documented and tested:

    - **Sub-second time is truncated.** FIT record timestamps are whole-second
      resolution, so a point's fractional second is dropped (like GPX speed and
      TCX temperature are documented losses).
    - **Temperature is rounded to whole degrees.** FIT stores temperature as a
      signed byte at 1 degC resolution, so ``21.5`` degC reads back as ``21``.
      (Altitude and speed quantise too, to 0.2 m and 0.001 m/s, but those are
      below GPS noise.)
    - **Device attribution is best-effort.** ``serial_number``, ``time_created``,
      and the device name (as free-text ``product_name``) are written; the
      manufacturer is mapped back to the FIT enum only when its name matches, else
      omitted. A perfect :class:`Device` round-trip is not claimed.
    - **An unmapped sport is omitted** rather than guessed, so it reads as
      ``None``.
    - **A reading equal to the FIT "invalid" sentinel reads back as absent.** FIT
      marks a missing channel with the type's maximum, so a real value that lands
      on it (heart rate or cadence ``255``, power ``65535``, temperature ``127``)
      comes back ``None``. These are physically implausible, so the value is
      written as-is rather than nudged.

    A channel value beyond what FIT can store at all (for example power above
    ``65535`` W) cannot be written: rather than a raw fit-tool traceback, a clear
    :class:`~gpsmerge.errors.WriteError` is raised naming the point (see
    :func:`_build_record`).

    Parameters
    ----------
    activity : Activity
        The activity to serialise.
    path : Path
        Destination file path; overwritten if it exists. (The caller handles
        atomicity; this function only builds and writes the file it is given.)

    Raises
    ------
    RuntimeError
        If the optional ``fit-tool`` dependency is not installed, with guidance
        on how to install it.
    WriteError
        If a channel value falls outside the range FIT can store.
    """
    fit = _load_fit_tool()
    builder = fit.FitFileBuilder(auto_define=True)
    for message in _build_messages(activity, fit):
        builder.add(message)
    builder.build().to_file(str(path))


class _FitTool:
    """The slice of the optional ``fit-tool`` package the writer uses.

    fit-tool is imported lazily (inside :func:`write_fit`) so the read path keeps
    working with only fitdecode installed. Loading every class and enum once into
    this small container keeps the message builders below readable and means the
    import lives in exactly one place.
    """

    def __init__(self) -> None:
        from fit_tool.fit_file_builder import FitFileBuilder
        from fit_tool.profile.messages.activity_message import ActivityMessage
        from fit_tool.profile.messages.event_message import EventMessage
        from fit_tool.profile.messages.file_id_message import FileIdMessage
        from fit_tool.profile.messages.lap_message import LapMessage
        from fit_tool.profile.messages.record_message import RecordMessage
        from fit_tool.profile.messages.session_message import SessionMessage
        from fit_tool.profile.messages.sport_message import SportMessage
        from fit_tool.profile.profile_type import (
            Event,
            EventType,
            FileType,
            LapTrigger,
            Manufacturer,
            Sport,
        )

        self.FitFileBuilder = FitFileBuilder
        self.ActivityMessage = ActivityMessage
        self.EventMessage = EventMessage
        self.FileIdMessage = FileIdMessage
        self.LapMessage = LapMessage
        self.RecordMessage = RecordMessage
        self.SessionMessage = SessionMessage
        self.SportMessage = SportMessage
        self.Event = Event
        self.EventType = EventType
        self.FileType = FileType
        self.LapTrigger = LapTrigger
        self.Manufacturer = Manufacturer
        self.Sport = Sport


def _load_fit_tool() -> _FitTool:
    """Import the optional ``fit-tool`` package, or raise a helpful error.

    fit-tool is an optional extra (``pip install 'gpsmerge[fit]'``); the read path
    does not need it. We import it only when actually writing FIT, and turn a
    missing import into an actionable :class:`RuntimeError` rather than letting an
    opaque ``ImportError`` surface.
    """
    try:
        return _FitTool()
    except ImportError as exc:
        raise RuntimeError(_FIT_TOOL_MISSING) from exc


def _build_messages(activity: Activity, fit: _FitTool) -> list[object]:
    """Build the ordered FIT message stream for `activity`.

    The order is the one :func:`read_fit` (and real devices) expect: identity and
    sport first, then the timer-bracketed records grouped by segment, then the lap
    overlay, then the single session and activity summaries.
    """
    start = _activity_start(activity)
    messages: list[object] = [_build_file_id(activity.device, start, fit)]
    sport = _build_sport(activity.sport, fit)
    if sport is not None:
        messages.append(sport)
    messages.extend(_build_records(activity, fit))
    messages.extend(_build_laps(activity.laps, fit))
    messages.append(_build_session(activity, start, fit))
    messages.append(_build_activity(activity, fit))
    return messages


def _activity_start(activity: Activity) -> datetime | None:
    """Return the first point's time, or the activity's creation time as a fallback."""
    for point in activity.points():
        if point.time is not None:
            return point.time
    return activity.time_created


def _build_file_id(device: Device, start: datetime | None, fit: _FitTool) -> object:
    """Build the ``file_id`` message: file type, device attribution, creation time.

    Device attribution is best-effort. The serial number and the device name (as
    free-text ``product_name``) are written verbatim; the manufacturer is mapped
    back to the FIT enum only when its name matches, otherwise omitted. We never
    force an unknown maker onto a wrong code.
    """
    message = fit.FileIdMessage()
    message.type = fit.FileType.ACTIVITY.value
    if start is not None:
        message.time_created = _to_millis(start)
    if device.serial_number is not None:
        message.serial_number = device.serial_number
    if device.name is not None:
        message.product_name = device.name
    manufacturer = _manufacturer_value(device.manufacturer, fit)
    if manufacturer is not None:
        message.manufacturer = manufacturer
    return message


def _manufacturer_value(manufacturer: str | None, fit: _FitTool) -> int | None:
    """Return the FIT ``Manufacturer`` code for `manufacturer`, or ``None``.

    The model stores a decoded name like ``"garmin"`` or ``"stages_cycling"``; we
    map it back to the enum by upper-casing to its member name. A maker the enum
    does not know is omitted (``None``) rather than guessed at.
    """
    if manufacturer is None:
        return None
    member = fit.Manufacturer.__members__.get(manufacturer.upper())
    return int(member.value) if member is not None else None


def _build_sport(sport: str | None, fit: _FitTool) -> object | None:
    """Build a ``sport`` message, or ``None`` for an absent or unmapped sport.

    A sport we have no FIT enum for is dropped rather than guessed, so it reads
    back as ``None`` instead of a wrong sport.
    """
    member_name = _SPORT_TO_FIT.get(sport) if sport is not None else None
    if member_name is None:
        return None
    message = fit.SportMessage()
    message.sport = fit.Sport[member_name].value
    return message


def _build_records(activity: Activity, fit: _FitTool) -> list[object]:
    """Build the timer-bracketed ``record`` stream across every segment.

    A timer ``start`` event opens the recording and one ``record`` is emitted per
    point. A timer ``stop_all`` event closes each segment; because :func:`read_fit`
    splits a new segment at every timer stop, emitting a stop between segments
    (and a final stop at the end) is what makes pauses survive the round-trip.
    Empty segments contribute nothing.
    """
    messages: list[object] = []
    started = False
    for segment in activity.segments:
        if not segment.points:
            continue
        first_time = _first_time(segment.points)
        if not started:
            messages.append(_build_timer(first_time, _EVENT_TYPE_START, fit))
            started = True
        for point in segment.points:
            messages.append(_build_record(point, fit))
        messages.append(_build_timer(_last_time(segment.points), _EVENT_TYPE_STOP, fit))
        started = False
    return messages


def _build_record(point: TrackPoint, fit: _FitTool) -> object:
    """Build one ``record`` message, setting only the channels the point carries.

    A ``None`` channel is left unset so fit-tool writes the FIT *invalid* sentinel
    and the reader maps it back to ``None``, so an absent channel is never written
    as ``0``.

    Raises
    ------
    WriteError
        If a channel value falls outside the range FIT can store. fit-tool rejects
        it on assignment with an opaque error; we turn that into a clear, actionable
        one naming the offending point, so the same activity that exports fine to
        GPX/TCX fails loudly rather than with a raw traceback on ``--format fit``.
    """
    message = fit.RecordMessage()
    try:
        _set_record_channels(message, point)
    except Exception as exc:
        # fit-tool raises a bare ``Exception`` for an unrepresentable value.
        raise WriteError(
            f"cannot write the point at {_point_label(point)} to FIT: {exc}"
        ) from exc
    return message


def _set_record_channels(message: Any, point: TrackPoint) -> None:
    """Set each channel the point carries on `message`, in real-world units.

    Every channel is set in real-world units (degrees, metres, m/s, bpm, rpm, W,
    degC); fit-tool applies the FIT scale/offset. Absent channels are skipped (see
    :func:`_build_record`).
    """
    if point.time is not None:
        message.timestamp = _to_millis(point.time)
    if point.lat is not None:
        message.position_lat = point.lat
    if point.lon is not None:
        message.position_long = point.lon
    if point.ele is not None:
        message.altitude = point.ele
    if point.distance is not None:
        message.distance = point.distance
    if point.hr is not None:
        message.heart_rate = point.hr
    if point.cad is not None:
        message.cadence = point.cad
    if point.power is not None:
        message.power = point.power
    if point.temp is not None:
        message.temperature = point.temp
    if point.speed is not None:
        message.speed = point.speed


def _point_label(point: TrackPoint) -> str:
    """Return a label for a point in an error: its UTC time, or that it has none."""
    return point.time.isoformat() if point.time is not None else "an untimed point"


def _build_timer(when: datetime | None, event_type_name: str, fit: _FitTool) -> object:
    """Build a timer event at `when`, of the named ``EventType`` (a segment boundary).

    `event_type_name` is a FIT ``EventType`` member name (``"START"`` or
    ``"STOP_ALL"``); resolving it by name here keeps the enum import lazy.
    """
    message = fit.EventMessage()
    if when is not None:
        message.timestamp = _to_millis(when)
    message.event = fit.Event.TIMER.value
    message.event_type = fit.EventType[event_type_name].value
    return message


def _build_laps(laps: list[Lap], fit: _FitTool) -> list[object]:
    """Build one ``lap`` message per model lap, in start-time order.

    A FIT lap stores its start under ``start_time`` and its end under the message
    ``timestamp``, mirroring how :func:`read_fit` reads them back. The trigger is
    mapped to the FIT enum (an unknown trigger falls back to ``MANUAL``). A lap
    with no end runs to its start, so the span is always well-formed.
    """
    ordered = sorted(laps, key=lambda lap: lap.start_time)
    return [_build_lap(index, lap, fit) for index, lap in enumerate(ordered)]


def _build_lap(index: int, lap: Lap, fit: _FitTool) -> object:
    """Build one ``lap`` message: index, span, trigger, and recomputed elapsed time."""
    end = lap.end_time if lap.end_time is not None else lap.start_time
    message = fit.LapMessage()
    message.message_index = index
    message.start_time = _to_millis(lap.start_time)
    message.timestamp = _to_millis(end)
    message.lap_trigger = _lap_trigger_value(lap.trigger, fit)
    elapsed = (end - lap.start_time).total_seconds()
    message.total_elapsed_time = elapsed
    message.total_timer_time = elapsed
    return message


def _lap_trigger_value(trigger: str | None, fit: _FitTool) -> int:
    """Return the FIT ``LapTrigger`` code for `trigger`, defaulting to ``MANUAL``."""
    member_name = _TRIGGER_TO_FIT.get(trigger or "", _DEFAULT_TRIGGER_FIT)
    return int(fit.LapTrigger[member_name].value)


def _build_session(activity: Activity, start: datetime | None, fit: _FitTool) -> object:
    """Build the single ``session`` summary, with totals recomputed from the points.

    A session carries the activity's span, sport, and totals plus the lap index
    range it covers. Totals are recomputed here rather than trusted from any
    stored value, so they always match the (possibly merged) data.
    """
    end = _activity_end(activity)
    message = fit.SessionMessage()
    message.message_index = 0
    if start is not None:
        message.start_time = _to_millis(start)
    if end is not None:
        message.timestamp = _to_millis(end)
    sport_name = _SPORT_TO_FIT.get(activity.sport or "")
    if sport_name is not None:
        message.sport = fit.Sport[sport_name].value
    elapsed = _elapsed_seconds(start, end)
    message.total_elapsed_time = elapsed
    message.total_timer_time = _moving_seconds(activity)
    message.total_distance = _total_distance(activity)
    message.num_laps = max(len(activity.laps), 1)
    message.first_lap_index = 0
    return message


def _build_activity(activity: Activity, fit: _FitTool) -> object:
    """Build the single ``activity`` summary that closes the file."""
    end = _activity_end(activity)
    message = fit.ActivityMessage()
    if end is not None:
        message.timestamp = _to_millis(end)
    message.num_sessions = 1
    message.total_timer_time = _moving_seconds(activity)
    return message


def _activity_end(activity: Activity) -> datetime | None:
    """Return the last recorded point's time, or ``None`` when none carry a time."""
    times = [p.time for p in activity.points() if p.time is not None]
    return max(times) if times else None


def _elapsed_seconds(start: datetime | None, end: datetime | None) -> float:
    """Return wall-clock seconds from `start` to `end`, or ``0`` if either is absent."""
    if start is None or end is None:
        return 0.0
    return (end - start).total_seconds()


def _moving_seconds(activity: Activity) -> float:
    """Sum the recorded time within each segment, excluding pauses between them."""
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


def _first_time(points: list[TrackPoint]) -> datetime | None:
    """Return the first point's time, or ``None`` if none carry one."""
    for point in points:
        if point.time is not None:
            return point.time
    return None


def _last_time(points: list[TrackPoint]) -> datetime | None:
    """Return the last point's time, or ``None`` if none carry one."""
    for point in reversed(points):
        if point.time is not None:
            return point.time
    return None


def _to_millis(value: datetime) -> int:
    """Return milliseconds since the Unix epoch for `value`.

    fit-tool sets timestamps in milliseconds since the Unix epoch and converts to
    the FIT epoch internally. The sub-second part of `value` is truncated to a
    whole second here because FIT record timestamps are whole-second resolution --
    a documented FIT-write loss.

    A naive `value` is taken to be UTC, mirroring the read side (every reader
    normalises to tz-aware UTC); without this a naive datetime would be read in the
    machine's local zone and encode a wrong instant.
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    whole_seconds = int(value.timestamp())
    return whole_seconds * _MILLISECONDS_PER_SECOND
