"""Detect a donor device's clock offset for stacking.

When two devices record the same ride, their clocks rarely agree to the second.
Stacking borrows a donor's sensor channels onto the master's timeline by
timestamp, so a donor clock that runs a few seconds fast or slow smears the
borrowed channel -- power lands a moment late, heart rate a moment early. This
module measures that offset.

It does so by cross-correlating one channel the two activities *both* record:
slide the donor's series against the master's, second by second, and keep the
shift that lines the two shapes up best. The shift is reported as the number of
seconds to add to the donor's timestamps, which :mod:`gpsmerge.merge` then
applies to a copy of the donor before borrowing from it. The master is never
moved.

Pure Python and model-aware only through reading points; the correlation itself
works on plain ``(time_seconds, value)`` numbers and reuses
:func:`gpsmerge.util.resample.lerp` to sample each side on a common grid.
"""

from __future__ import annotations

from gpsmerge.model import Activity
from gpsmerge.util.resample import lerp

# How far, in seconds, a donor clock is allowed to be off. Real devices drift by
# a handful of seconds; searching wider only invites a spurious correlation peak.
DEFAULT_MAX_SKEW_S = 30.0

# The best lag is only trusted when its Pearson correlation clears this bar.
# Below it the two shapes do not really match (different channels, noise, too
# little signal), so we decline to shift rather than align on a guess.
CONFIDENCE_THRESHOLD = 0.5

# Channels worth correlating on, best first. Speed and power vary briskly and
# track the effort closely, so they pin the offset sharply; elevation is the last
# resort because it changes slowly and aligns loosely.
_CORRELATION_CHANNELS = ("speed", "power", "hr", "cad", "ele")


def best_shared_channel(master: Activity, donor: Activity) -> str | None:
    """Return the best channel to correlate the two activities on, or ``None``.

    A channel is usable only when both activities record it *and* it actually
    varies on both sides -- a pinned, constant signal carries no shape to align.
    Among the usable channels the first in priority order
    (speed, power, hr, cad, ele) is chosen.

    Parameters
    ----------
    master : Activity
        The activity that keeps its timeline; the donor is aligned onto it.
    donor : Activity
        The activity whose clock offset is being measured.

    Returns
    -------
    str or None
        The channel name to correlate on, or ``None`` when no shared channel
        varies on both sides.
    """
    for channel in _CORRELATION_CHANNELS:
        if _varies(master, channel) and _varies(donor, channel):
            return channel
    return None


def detect_clock_offset(
    master: Activity, donor: Activity, max_skew_s: float = DEFAULT_MAX_SKEW_S
) -> float:
    """Return the seconds to add to the donor's timestamps to align it to the master.

    A donor clock that runs *fast* stamps each reading later than it happened, so
    its series must be moved *earlier* to match the master: the returned offset is
    then negative. A *slow* donor needs a positive offset. The number is found by
    cross-correlating a channel both activities record: for each integer candidate
    lag ``L`` in ``[-max_skew_s, +max_skew_s]`` the donor is sampled at
    ``grid_t - L`` and the master at ``grid_t`` on a shared 1 Hz grid, and the
    ``L`` whose samples correlate best is returned. Sampling the donor at
    ``grid_t - L`` tests the alignment that *adding* ``L`` to the donor's
    timestamps would produce, so the winning ``L`` is exactly the offset to add.

    Parameters
    ----------
    master : Activity
        The activity that provides the reference timeline.
    donor : Activity
        The activity whose clock offset is being measured.
    max_skew_s : float, optional
        The widest offset, in seconds, to search in either direction
        (default :data:`DEFAULT_MAX_SKEW_S`).

    Returns
    -------
    float
        The seconds to add to every donor timestamp. ``0.0`` when there is no
        shared varying channel, too little time overlap to grid, or the best
        correlation does not clear :data:`CONFIDENCE_THRESHOLD` -- we do not shift
        on a guess.
    """
    channel = best_shared_channel(master, donor)
    if channel is None:
        return 0.0

    master_series = master.channel_series(channel)
    donor_series = donor.channel_series(channel)
    grid = _overlap_grid(master_series, donor_series)
    if grid is None:
        return 0.0

    master_on_grid = lerp(grid, master_series, _GRID_MAX_GAP_S)
    best_lag = 0
    best_corr = CONFIDENCE_THRESHOLD
    max_lag = int(max_skew_s)
    for lag in range(-max_lag, max_lag + 1):
        donor_on_grid = lerp([t - lag for t in grid], donor_series, _GRID_MAX_GAP_S)
        corr = _correlation(master_on_grid, donor_on_grid)
        if corr is not None and corr > best_corr:
            best_corr, best_lag = corr, lag

    return float(best_lag)


# The shared grid is 1 Hz, so a value is interpolated across at most this many
# seconds; wider than two steps is a real dropout and lerp leaves it ``None``.
_GRID_MAX_GAP_S = 5.0


def _varies(activity: Activity, channel: str) -> bool:
    """Return whether a channel has a non-zero spread across the activity's points."""
    values = [
        float(getattr(p, channel))
        for p in activity.points()
        if getattr(p, channel) is not None
    ]
    return len(values) >= 2 and max(values) > min(values)


def _overlap_grid(
    master_series: list[tuple[float, float]],
    donor_series: list[tuple[float, float]],
) -> list[float] | None:
    """Return a 1 Hz grid of times the two series both span, or ``None`` if none.

    The grid runs over the wall-clock window both series cover, so every grid
    point can in principle be sampled from each side. Fewer than two grid points
    is too little to correlate.
    """
    if not master_series or not donor_series:
        return None
    start = max(master_series[0][0], donor_series[0][0])
    end = min(master_series[-1][0], donor_series[-1][0])
    if end - start < 1.0:
        return None
    return [float(t) for t in range(int(start), int(end) + 1)]


def _correlation(xs: list[float | None], ys: list[float | None]) -> float | None:
    """Return the Pearson correlation over grid points present on both sides.

    Only positions where both series produced a value count. Returns ``None``
    when fewer than two such positions remain or either side is flat there (a
    zero standard deviation makes the coefficient undefined).
    """
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pairs) < 2:
        return None

    n = len(pairs)
    mean_x = sum(x for x, _ in pairs) / n
    mean_y = sum(y for _, y in pairs) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in pairs)
    var_x = sum((x - mean_x) ** 2 for x, _ in pairs)
    var_y = sum((y - mean_y) ** 2 for _, y in pairs)
    if var_x == 0.0 or var_y == 0.0:
        return None
    return cov / (var_x * var_y) ** 0.5
