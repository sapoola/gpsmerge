"""Great-circle geometry and FIT semicircle conversions.

These are the small, format-agnostic geometry helpers the merge and parsing
code share: distance between two WGS84 points, the running distance along a
track, and the integer "semicircle" angle units that FIT files store positions
in. Keeping them here means a writer that needs metres and a FIT reader that
needs degrees both call the same tested functions.
"""

from __future__ import annotations

import math
import re

from gpsmerge.model import TrackPoint

# IUGG mean Earth radius in metres. Using one fixed sphere keeps haversine
# distances reproducible across readers and writers.
EARTH_RADIUS_M: float = 6_371_008.8

# A distance like "1km", "500m", "0.5mi", "1mi", or a bare "1000". The unit is
# optional and defaults to metres; the value may carry decimals. Case and
# surrounding whitespace do not matter (handled in :func:`parse_distance`).
_DISTANCE_RE = re.compile(r"^(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>km|mi|m)?$")

# Metres per supported distance unit. A bare number with no unit is metres.
_DISTANCE_UNIT_METRES: dict[str, float] = {"m": 1.0, "km": 1000.0, "mi": 1609.344}

# FIT stores latitude and longitude as signed 32-bit "semicircles", where a
# full circle is 2**32 units. 2**31 semicircles is therefore 180 degrees.
_SEMICIRCLES_PER_HALF_TURN: int = 2**31
_DEGREES_PER_HALF_TURN: float = 180.0

# Bounds of the signed int32 a FIT position field can hold. +180.0 rounds to
# 2**31, which would overflow this field, so the conversion clamps to these.
_INT32_MIN: int = -(2**31)
_INT32_MAX: int = 2**31 - 1


def parse_distance(spec: str) -> float:
    """Parse a distance spec into metres.

    Accepts ``"1km"``, ``"500m"``, ``"0.5mi"``, ``"1mi"``, or a bare number such
    as ``"1000"`` (taken as metres). The unit is case-insensitive and may be
    separated from the value by spaces. The result must be strictly positive --
    a zero or negative interval cannot split a track into laps.

    Parameters
    ----------
    spec : str
        A distance with an optional ``km`` / ``mi`` / ``m`` suffix.

    Returns
    -------
    float
        The distance in metres.

    Raises
    ------
    ValueError
        If `spec` is not a recognised distance, or is not strictly positive.
    """
    cleaned = spec.strip().lower()
    if not cleaned:
        raise ValueError("distance spec is empty")

    match = _DISTANCE_RE.match(cleaned)
    if match is None:
        raise ValueError(
            f"could not understand distance {spec!r}; use e.g. '1km', '500m', "
            "'0.5mi', or '1mi'"
        )

    metres = float(match["value"]) * _DISTANCE_UNIT_METRES[match["unit"] or "m"]
    if metres <= 0:
        raise ValueError(f"distance must be greater than zero, got {spec!r}")
    return metres


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return the great-circle distance between two WGS84 points, in metres.

    Parameters
    ----------
    lat1, lon1 : float
        Latitude and longitude of the first point, in decimal degrees.
    lat2, lon2 : float
        Latitude and longitude of the second point, in decimal degrees.

    Returns
    -------
    float
        Distance along the sphere of radius `EARTH_RADIUS_M`, in metres. Zero
        when the two points coincide.
    """
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    c = 2 * math.asin(math.sqrt(a))
    return EARTH_RADIUS_M * c


def cumulative_distance(points: list[TrackPoint]) -> list[float]:
    """Return the running distance from the first point, in metres.

    The result is the same length as `points`, starts at ``0.0``, and never
    decreases. Each step adds the haversine distance between two consecutive
    points that both carry a position. A point missing latitude or longitude
    has no usable fix, so it carries the previous total forward unchanged and
    the next positioned point measures across the gap rather than producing a
    spurious jump.

    Parameters
    ----------
    points : list of TrackPoint
        Track points in recorded order.

    Returns
    -------
    list of float
        Cumulative metres from `points[0]`; ``[]`` for empty input.
    """
    if not points:
        return []

    totals: list[float] = [0.0]
    last_pos: tuple[float, float] | None = _position(points[0])

    for point in points[1:]:
        pos = _position(point)
        if pos is None:
            totals.append(totals[-1])
            continue
        if last_pos is None:
            totals.append(totals[-1])
        else:
            totals.append(totals[-1] + haversine(*last_pos, *pos))
        last_pos = pos

    return totals


def _position(point: TrackPoint) -> tuple[float, float] | None:
    """Return the point's ``(lat, lon)`` if both are present, else ``None``."""
    if point.lat is None or point.lon is None:
        return None
    return point.lat, point.lon


def semicircles_to_degrees(semicircles: int) -> float:
    """Convert a FIT semicircle angle to decimal degrees.

    Parameters
    ----------
    semicircles : int
        Angle in FIT semicircles (a full circle is ``2**32``).

    Returns
    -------
    float
        The angle in decimal degrees.
    """
    return semicircles * (_DEGREES_PER_HALF_TURN / _SEMICIRCLES_PER_HALF_TURN)


def degrees_to_semicircles(degrees: float) -> int:
    """Convert decimal degrees to a FIT semicircle angle.

    The result is clamped to the signed int32 range a FIT position field holds,
    so ``+180.0`` (which rounds to ``2**31``) does not overflow to a negative
    angle.

    Parameters
    ----------
    degrees : float
        Angle in decimal degrees.

    Returns
    -------
    int
        The angle in FIT semicircles, within ``[-2**31, 2**31 - 1]``.
    """
    raw = round(degrees * _SEMICIRCLES_PER_HALF_TURN / _DEGREES_PER_HALF_TURN)
    clamped = max(_INT32_MIN, min(_INT32_MAX, raw))
    return int(clamped)
