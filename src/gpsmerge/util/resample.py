"""Linear interpolation of a single sensor channel onto target times.

When two activities are merged, one is the donor of a channel (heart rate,
power, ...) the other lacks. The donor was sampled on its own clock, so its
values are projected onto the target activity's timestamps. This module does that
projection for one channel, in seconds, as plain floats: callers pass and receive
``(time_seconds, value)`` numbers, and the merge layer does any rounding (e.g.
heart rate back to an ``int``).
"""

from __future__ import annotations

from bisect import bisect_right


def lerp(
    target_times: list[float],
    src: list[tuple[float, float]],
    max_gap_s: float,
) -> list[float | None]:
    """Linearly interpolate one channel onto target times.

    For each target time, find the donor samples that bracket it and return the
    straight-line value between them. A bracket wider than `max_gap_s` is treated
    as a real recording dropout and not filled.

    Parameters
    ----------
    target_times : list of float
        Times, in seconds, to produce a value for. Order is preserved in the
        result; they need not be sorted.
    src : list of tuple of (float, float)
        Donor ``(time_seconds, value)`` samples, sorted ascending by time. May be
        empty.
    max_gap_s : float
        Largest bracket width, in seconds, across which interpolation is allowed.
        A bracket exactly this wide still interpolates; wider yields ``None``.

    Returns
    -------
    list of float or None
        One value per target time. ``None`` where `src` is empty, the target lies
        outside the donor's time range, or the bracketing samples are more than
        `max_gap_s` apart. Values are returned as floats; rounding integer
        channels such as heart rate is the caller's job, not done here.
    """
    if not src:
        return [None] * len(target_times)

    # Split the (time, value) pairs into parallel lists once, up front. A single
    # call may carry tens of thousands of target times (a whole stack resampled at
    # once); rebuilding these per target would make that call O(n*m) for no reason.
    src_times = [t for t, _ in src]
    src_values = [v for _, v in src]
    first_time = src_times[0]
    last_time = src_times[-1]

    return [
        _interpolate_one(t, src_times, src_values, first_time, last_time, max_gap_s)
        for t in target_times
    ]


def _interpolate_one(
    t: float,
    src_times: list[float],
    src_values: list[float],
    first_time: float,
    last_time: float,
    max_gap_s: float,
) -> float | None:
    """Interpolate a single target time, or return ``None`` if it cannot be."""
    if t < first_time or t > last_time:
        return None

    # bisect_right gives the first sample strictly after t, so the sample before
    # it is the left edge of the bracket. t == last_time lands past the end.
    right_index = bisect_right(src_times, t)
    if right_index == len(src_times):
        return src_values[-1]  # exact hit on the final sample

    a_time, a_value = src_times[right_index - 1], src_values[right_index - 1]
    b_time, b_value = src_times[right_index], src_values[right_index]

    if a_time == t:
        return a_value  # exact hit, no interpolation needed

    if b_time - a_time > max_gap_s:
        return None  # a real dropout; do not invent data across it

    fraction = (t - a_time) / (b_time - a_time)
    return a_value + fraction * (b_value - a_value)
