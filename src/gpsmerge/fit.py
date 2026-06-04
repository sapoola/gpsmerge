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
from typing import Any

import fitdecode

from gpsmerge.geo import semicircles_to_degrees
from gpsmerge.model import Activity, Device, Segment, TrackPoint

SOURCE_FORMAT = "fit"

# FIT event messages name the subsystem in ``event`` and what it did in
# ``event_type``. A timer stop (or stop_all) marks a pause; the next timer start
# resumes recording. These are the boundaries between our segments.
_TIMER_EVENT = "timer"
_STOP_EVENT_TYPES = frozenset({"stop", "stop_all"})

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

    with fitdecode.FitReader(path) as reader:
        for frame in reader:
            if not isinstance(frame, fitdecode.FitDataMessage):
                continue
            if frame.name == "record":
                current.points.append(_read_point(frame))
            elif frame.name == "file_id":
                device = _read_device(frame)
                time_created = _get(frame, "time_created")
            elif frame.name in ("sport", "session") and sport is None:
                sport = _canonical_sport(_get(frame, "sport"))
            elif frame.name == "event" and _is_timer_stop(frame):
                current = _close_segment(segments, current)

    if current.points:
        segments.append(current)

    return Activity(
        sport=sport,
        device=device,
        segments=segments,
        time_created=_to_utc(time_created),
        source_format=SOURCE_FORMAT,
        source_path=str(path),
    )


def _read_point(frame: fitdecode.FitDataMessage) -> TrackPoint:
    """Convert one ``record`` message to a TrackPoint, in real units."""
    return TrackPoint(
        time=_to_utc(_get(frame, "timestamp")),
        lat=_position_degrees(_get(frame, "position_lat")),
        lon=_position_degrees(_get(frame, "position_long")),
        ele=_prefer(_get(frame, "enhanced_altitude"), _get(frame, "altitude")),
        distance=_get(frame, "distance"),
        hr=_get(frame, "heart_rate"),
        cad=_get(frame, "cadence"),
        power=_get(frame, "power"),
        temp=_get(frame, "temperature"),
        speed=_prefer(_get(frame, "enhanced_speed"), _get(frame, "speed")),
    )


def _read_device(frame: fitdecode.FitDataMessage) -> Device:
    """Read the recording device from a ``file_id`` message.

    fitdecode decodes manufacturer and product to readable strings (e.g.
    ``"garmin"`` and ``"edge_530"``). For Garmin devices the product lives under
    ``garmin_product`` rather than the generic ``product`` field, so both are
    checked. The human ``name`` joins the two when both are known.
    """
    manufacturer = _stringify(_get(frame, "manufacturer"))
    product = _stringify(_get(frame, "product") or _get(frame, "garmin_product"))
    return Device(
        name=_device_name(manufacturer, product),
        manufacturer=manufacturer,
        product=product,
        serial_number=_get(frame, "serial_number"),
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
        _get(frame, "event") == _TIMER_EVENT
        and _get(frame, "event_type") in _STOP_EVENT_TYPES
    )


def _close_segment(segments: list[Segment], current: Segment) -> Segment:
    """Emit `current` if it has points and return a fresh empty segment.

    A timer stop with no points since the last boundary (e.g. two stops in a row)
    adds no empty segment, so spurious boundaries do not create empty runs.
    """
    if current.points:
        segments.append(current)
    return Segment()


def _canonical_sport(sport: Any) -> str | None:
    """Map a decoded FIT sport to our canonical sport, or ``None`` if unknown."""
    name = _stringify(sport)
    if name is None:
        return None
    name = name.lower()
    return name if name in _CANONICAL_SPORTS else None


def _position_degrees(semicircles: int | None) -> float | None:
    """Convert a semicircle position to degrees, or ``None`` if absent."""
    if semicircles is None:
        return None
    return semicircles_to_degrees(semicircles)


def _prefer(enhanced: float | None, plain: float | None) -> float | None:
    """Return `enhanced` when present, else `plain` (which may itself be ``None``)."""
    return enhanced if enhanced is not None else plain


def _get(frame: fitdecode.FitDataMessage, field: str) -> Any:
    """Return a decoded field's value, or ``None`` when the record lacks it.

    fitdecode raises if you ask for a field a message does not carry, so this
    wraps ``get_value`` with a ``None`` fallback -- making an absent channel read
    as ``None`` rather than an error.
    """
    return frame.get_value(field, fallback=None)


def _stringify(value: Any) -> str | None:
    """Return a non-empty string for `value`, or ``None`` when it is absent."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


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
