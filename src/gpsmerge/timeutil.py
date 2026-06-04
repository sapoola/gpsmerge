"""Timestamp helpers shared by the parsers, writers, and the preview display.

Pure stdlib (no model import). Most functions speak in timezone-aware UTC
datetimes, matching the model's rule that all timestamps are UTC. Naive input is
assumed to already be UTC -- the file formats we read either state UTC explicitly
or, when they omit a zone, mean UTC. The formatting helpers at the bottom render
times for the command line and the wizard, which both show the same preview.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Unix seconds at 1989-12-31T00:00:00Z, the epoch FIT timestamps count from.
FIT_EPOCH_OFFSET_S: int = 631065600

# Spellings that all mean "no offset from UTC".
_UTC_ALIASES: frozenset[str] = frozenset({"utc", "z", "0", "+0", "-0"})

# A fixed offset like "+2", "-5", "+02:00", "-05:30". Hours may be one or two
# digits; minutes are optional and default to zero.
_OFFSET_RE = re.compile(r"^(?P<sign>[+-])(?P<hours>\d{1,2})(?::(?P<minutes>\d{2}))?$")

# Largest offset any real zone uses is +14:00; reject anything beyond a day.
_MAX_OFFSET_HOURS: int = 24


def parse_time(value: str) -> datetime:
    """Parse an ISO-8601 timestamp into a timezone-aware UTC datetime.

    Accepts a trailing ``Z``, a numeric offset such as ``+02:00``, or no zone.
    A value with no zone is assumed to be UTC. Offsets are converted to UTC.

    Parameters
    ----------
    value : str
        An ISO-8601 timestamp.

    Returns
    -------
    datetime
        The same instant expressed in timezone-aware UTC.

    Raises
    ------
    ValueError
        If `value` is not a parseable ISO-8601 timestamp.
    """
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def to_epoch_seconds(dt: datetime) -> float:
    """Return POSIX seconds for a datetime, keeping sub-second precision.

    Parameters
    ----------
    dt : datetime
        The datetime to convert. A naive `dt` is assumed to be UTC.

    Returns
    -------
    float
        Seconds since the Unix epoch, including any fractional part.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.timestamp()


def fit_timestamp_to_utc(seconds: int) -> datetime:
    """Convert a FIT timestamp to a timezone-aware UTC datetime.

    Parameters
    ----------
    seconds : int
        Seconds since the FIT epoch (1989-12-31T00:00:00Z).

    Returns
    -------
    datetime
        The instant as timezone-aware UTC.
    """
    return datetime.fromtimestamp(seconds + FIT_EPOCH_OFFSET_S, tz=UTC)


def utc_to_fit_timestamp(dt: datetime) -> int:
    """Convert a UTC datetime to a FIT timestamp.

    Inverse of :func:`fit_timestamp_to_utc`; sub-second precision is dropped
    because FIT timestamps are whole seconds.

    Parameters
    ----------
    dt : datetime
        The datetime to convert. A naive `dt` is assumed to be UTC.

    Returns
    -------
    int
        Seconds since the FIT epoch (1989-12-31T00:00:00Z).
    """
    return int(to_epoch_seconds(dt)) - FIT_EPOCH_OFFSET_S


def parse_timezone(spec: str) -> tzinfo:
    """Resolve a timezone spec to a :class:`~datetime.tzinfo` for local display.

    Two forms are accepted. A fixed UTC offset (``"+2"``, ``"+02:00"``, ``"-5"``,
    ``"-05:30"``, ``"0"``, ``"UTC"``, ``"Z"``) becomes a constant
    :class:`~datetime.timezone`. An IANA zone name (``"Europe/Amsterdam"``,
    ``"America/New_York"``) becomes a :class:`~zoneinfo.ZoneInfo`, which
    auto-resolves summer time / DST for whatever date it is applied to -- so the
    same name shows the correct offset in winter and in summer. This is
    display-only: it never changes the merged file's timestamps, which stay UTC.

    Parameters
    ----------
    spec : str
        A fixed offset or an IANA zone name.

    Returns
    -------
    tzinfo
        A fixed-offset ``timezone`` for an offset spec, or a ``ZoneInfo`` for an
        IANA name.

    Raises
    ------
    ValueError
        If `spec` is neither a recognised offset nor a known IANA zone name.
    """
    cleaned = spec.strip()
    if not cleaned:
        raise ValueError("timezone spec is empty")

    if cleaned.lower() in _UTC_ALIASES:
        return UTC

    offset = _parse_offset(cleaned)
    if offset is not None:
        return offset

    # Not an offset, so try it as an IANA name. ZoneInfo handles DST itself.
    try:
        return ZoneInfo(cleaned)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"unrecognised timezone: {spec!r}") from exc


def _parse_offset(spec: str) -> timezone | None:
    """Return a fixed-offset timezone for an offset spec, or ``None`` if it is not one.

    Raises
    ------
    ValueError
        If `spec` looks like an offset but is out of the supported range.
    """
    match = _OFFSET_RE.match(spec)
    if match is None:
        return None

    hours = int(match["hours"])
    minutes = int(match["minutes"] or 0)
    if hours >= _MAX_OFFSET_HOURS or minutes >= 60:
        raise ValueError(f"offset out of range: {spec!r}")

    delta = timedelta(hours=hours, minutes=minutes)
    if match["sign"] == "-":
        delta = -delta
    return timezone(delta)


def utc_range(start: datetime, end: datetime) -> str:
    """Format a UTC span as ``2026-06-04 08:00:00 -> 09:30:00 UTC``."""
    same_day = start.date() == end.date()
    end_text = end.strftime("%H:%M:%S") if same_day else _stamp(end)
    return f"{_stamp(start)} -> {end_text} UTC"


def local_range(start: datetime, end: datetime) -> str:
    """Format a local span the same way, without a trailing zone word."""
    same_day = start.date() == end.date()
    end_text = end.strftime("%H:%M:%S") if same_day else _stamp(end)
    return f"{_stamp(start)} -> {end_text}"


def zone_label(name: str | None, local_start: datetime) -> str:
    """Return e.g. ``Europe/Amsterdam, UTC+02:00`` for a local-time line.

    The offset is read from the resolved local time, so an IANA name shows the
    actual offset for the activity's date (summer time included).
    """
    offset = local_start.utcoffset()
    offset_text = _format_offset(offset) if offset is not None else "UTC"
    if name is None:
        return offset_text
    return f"{name}, {offset_text}"


def _stamp(value: datetime) -> str:
    """Format a datetime as ``YYYY-MM-DD HH:MM:SS`` (no zone suffix)."""
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _format_offset(offset: timedelta) -> str:
    """Format a UTC offset as ``UTC+02:00`` / ``UTC-05:30`` / ``UTC+00:00``."""
    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    hours, minutes = divmod(abs(total_minutes), 60)
    return f"UTC{sign}{hours:02d}:{minutes:02d}"
