"""Timestamp helpers shared by the parsers and writers.

Pure stdlib (no model import). Every function speaks in timezone-aware UTC
datetimes, matching the model's rule that all timestamps are UTC. Naive input is
assumed to already be UTC -- the file formats we read either state UTC explicitly
or, when they omit a zone, mean UTC.
"""

from __future__ import annotations

from datetime import UTC, datetime

# Unix seconds at 1989-12-31T00:00:00Z, the epoch FIT timestamps count from.
FIT_EPOCH_OFFSET_S: int = 631065600


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
