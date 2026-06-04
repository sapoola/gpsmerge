"""Read FIT (Garmin/ANT binary) activity files via fitdecode.

FIT is the compact binary format head units actually record to. fitdecode does
the heavy lifting -- it parses the typed message stream and decodes each field
for us: timestamps come back as timezone-aware UTC datetimes, manufacturer and
product come back as readable names, and scaled sensor channels (altitude in
metres, speed in m/s) arrive already in their real units. Position is the
exception -- it is semicircles and we convert it (see below). This module is the
small glue that maps those decoded messages onto the canonical :class:`Activity`.

Two FIT specifics are handled here rather than left to the model:

- **Position is in semicircles, not degrees.** The ``record`` message stores
  latitude and longitude as signed 32-bit integers; we convert them with
  :func:`gpsmerge.geo.semicircles_to_degrees`.
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

from gpsmerge.geo import semicircles_to_degrees
from gpsmerge.model import Activity, Device, Lap, Segment, TrackPoint

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
    that means nothing to a person, so it is dropped rather than appended -- the
    raw value is still kept in :attr:`Device.product`.
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
    wraps ``get_value`` with a ``None`` fallback -- making an absent channel read
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
