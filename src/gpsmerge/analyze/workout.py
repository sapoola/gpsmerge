"""Auto-detect a structured interval workout from a merged activity (BETA).

A trainer session or a track workout is really a sequence of held efforts --
"5 minutes at 300 W, 5 minutes at 180 W, 30 seconds at 500 W". The recording
carries every sample but no structure: the steps are implicit in the power (for
a ride) or speed (for a run) trace. This module recovers that structure as a
best-effort heuristic and expresses it two ways: as :class:`~gpsmerge.model.Lap`
overlays (so it persists into TCX/FIT, where laps round-trip) and as a short
human-readable summary line.

**This is BETA.** It is a heuristic, not a contract. It shines on structured
indoor/interval workouts and is honest about where it struggles (smooth ramps,
unstructured outdoor riding); see ``docs/workout-detection.md``.

The channel is chosen automatically, never forced by a flag:

- **power** for rides, when the activity has a *usable* power channel (real
  effort, judged by magnitude: a steady block counts, a dead-meter glitch does
  not);
- else **speed** for runs, when it has a *usable* speed channel;
- else a clear :class:`~gpsmerge.errors.WorkoutError`, because there is nothing
  to detect a workout from.

A boundary counts only when the level shifts by at least
``delta_min = max(FLOOR, K_AVG * ride_average)``, where ``ride_average`` is the
plain mean of the chosen channel's recorded samples. Scaling the threshold to the
ride's *own average* makes it self-calibrating: a rider whose ride averages ~110 W
treats a 100->130 W step as a real interval (+27% of average), while a rider whose
ride averages ~350 W treats the same +30 W as noise within one block (~8.5%). The
same applies to speed, scaled to the average speed.

numpy does the segmentation maths and is an *optional* extra
(``pip install 'gpsmerge[workout]'``); it is imported lazily inside the detector
so the core install stays numpy-free.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from gpsmerge.analyze.transforms import laps_from_boundaries
from gpsmerge.errors import WorkoutError
from gpsmerge.model import Activity, Lap
from gpsmerge.util.resample import lerp

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    import numpy as np


# --- tuning constants --------------------------------------------------------
#
# Tuning constants for the detection heuristic, gathered in one place.

# A real interval is a sustained change of at least this fraction of the ride's
# own average effort. 0.15 (15%) is the crux of the self-scaling design: it is
# small enough to catch a genuine step-up but large enough that within-block
# jitter on a hard ride does not fragment one effort into many.
K_AVG = 0.15

# Per-channel absolute floors, so a *very* easy ride (tiny average) still needs
# a real-world-sized change before a boundary counts. Without a floor, a ride
# averaging 40 W would call a 7 W wobble an interval.
FLOOR_POWER = 20.0  # watts
FLOOR_SPEED = 0.3  # metres per second (~ a 4:00 vs 4:30 /km pace step)

# The 1 Hz grid we resample onto, and the centred smoothing window applied to it
# (in seconds, forced odd). 5 s suppresses single-sample spikes without blurring
# a 20 s sprint into its neighbours.
GRID_HZ = 1.0
SMOOTH_WINDOW_S = 5

# How much signal we insist on before trusting a detection. Below either bound
# the trace is too thin to find structure in, so we raise the no-data error.
MIN_SECONDS = 60.0
MIN_COVERAGE = 0.5


@dataclass(slots=True)
class Interval:
    """One detected step of a workout: a held effort over a span.

    Attributes
    ----------
    start_time, end_time : datetime
        Timezone-aware UTC bounds of the step.
    average : float
        The mean of the *raw* (pre-smoothing) channel samples within the span,
        in the channel's native units (watts for power, m/s for speed).
    duration_seconds : float
        ``end_time - start_time`` in seconds, kept alongside for convenience.
    """

    start_time: datetime
    end_time: datetime
    average: float
    duration_seconds: float


@dataclass(slots=True)
class Workout:
    """The result of a detection: which channel, the steps, laps, and a summary.

    Attributes
    ----------
    channel : {"power", "speed"}
        The effort channel the workout was detected from.
    intervals : list of Interval
        The detected steps, in order.
    laps : list of Lap
        The same steps as :class:`~gpsmerge.model.Lap` overlays, built through
        the shared :func:`gpsmerge.analyze.transforms.laps_from_boundaries` helper, so
        they persist into TCX/FIT writes (both degrade the ``"power"``/``"speed"``
        trigger to ``"manual"`` on write).
    summary : str
        A one-line human-readable description, e.g.
        ``"5:00 @ 300 W, 5:00 @ 180 W"`` (power) or
        ``"3:00 @ 4:15/km, 1:00 @ 5:30/km"`` (speed, in pace per km).
    """

    channel: str
    intervals: list[Interval]
    laps: list[Lap]
    summary: str


def detect_workout(activity: Activity, *, min_seconds: float = 10.0) -> Workout:
    """Detect a structured interval workout from an activity's effort channel.

    The effort channel is chosen automatically: power if the activity has a
    varying power channel, else speed if it has a varying speed channel, else a
    :class:`~gpsmerge.errors.WorkoutError`. The chosen channel is resampled onto
    a 1 Hz grid, smoothed, segmented into piecewise-constant steps, and the steps
    are post-processed so only changes of at least
    ``max(FLOOR, K_AVG * ride_average)`` survive as boundaries (this is the
    ride-average self-scaling described in the module docstring). Any segment
    shorter than ``min_seconds`` is then absorbed into a neighbour, so that knob
    sets the shortest interval reported and is the one lever for a coarser overlay.

    Parameters
    ----------
    activity : Activity
        The merged activity to analyse. Not mutated.
    min_seconds : float, optional
        The shortest interval worth reporting: segments shorter than this are
        absorbed into a neighbour, so raising it yields fewer, longer intervals.
        Default ``10``.

    Returns
    -------
    Workout
        The chosen channel, the detected intervals, the laps overlay, and the
        summary line.

    Raises
    ------
    WorkoutError
        If neither power nor speed varies usably, or the chosen channel has too
        little signal (less than ``MIN_SECONDS`` of data or below ``MIN_COVERAGE``
        coverage of its span) to detect a workout from.
    RuntimeError
        If the optional ``numpy`` dependency is not installed.
    """
    return WorkoutDetector().detect(activity, min_seconds=min_seconds)


class WorkoutDetector:
    """Recovers interval structure from an activity's power or speed channel.

    Owns the lazily-loaded ``numpy`` module the segmentation maths runs on, so
    the per-step helpers share it through ``self`` rather than threading it as a
    parameter. Construct one and call :meth:`detect`.
    """

    def __init__(self) -> None:
        self._np = _load_numpy()

    def detect(self, activity: Activity, *, min_seconds: float = 10.0) -> Workout:
        """Detect a workout from ``activity``; see :func:`detect_workout`."""
        numpy = self._np
        channel = _select_channel(activity)

        times, grid = _build_grid(activity, channel)  # raw 1 Hz series (with gaps)
        raw = numpy.array(grid, dtype=float)
        self._require_enough_signal(raw)

        filled = self._fill_gaps(raw)
        smoothed = self._smooth(filled)

        ride_average = float(numpy.nanmean(raw))
        delta_min = max(_floor(channel), K_AVG * abs(ride_average))

        bounds = self._segment(smoothed, delta_min, min_seconds)
        intervals = self._build_intervals(times, raw, bounds)

        boundaries = [iv.start_time for iv in intervals]
        laps = laps_from_boundaries(
            boundaries, activity_end=intervals[-1].end_time, trigger=channel
        )
        summary = _summarise(intervals, channel)
        return Workout(channel=channel, intervals=intervals, laps=laps, summary=summary)

    def _require_enough_signal(self, raw: np.ndarray) -> None:
        """Raise if the resampled trace is too thin to detect a workout from.

        Two cheap checks: the span must be at least ``MIN_SECONDS`` long, and at
        least ``MIN_COVERAGE`` of the grid points must carry a (non-``NaN``)
        value. Below either, the trace is too sparse to trust, so the same clear
        no-data error is raised rather than returning a fabricated workout.
        """
        numpy = self._np
        present = int(numpy.count_nonzero(~numpy.isnan(raw)))
        span_seconds = len(raw) / GRID_HZ
        coverage = present / len(raw) if len(raw) else 0.0
        if span_seconds < MIN_SECONDS or coverage < MIN_COVERAGE or present < 2:
            raise WorkoutError(
                "not enough effort data to detect a workout from: the activity "
                f"needs at least {int(MIN_SECONDS)} seconds of recorded power or "
                f"speed with reasonable coverage (got {span_seconds:.0f} s, "
                f"{coverage * 100:.0f}% covered)"
            )

    def _fill_gaps(self, raw: np.ndarray) -> np.ndarray:
        """Return a gap-free copy of the trace, filling short ``NaN`` runs.

        Smoothing and segmentation want a contiguous array. Short gaps (already
        bounded by lerp's max-gap blanking upstream) are filled by carrying the
        last known value forward, then back-filling any leading ``NaN`` from the
        first known value, so the array is finite everywhere without inventing
        structure.

        Carry-forward bridges a gap rather than breaking at it, so a *long*
        recording dropout (minutes with no power or speed) is folded into the
        neighbouring interval: that interval's duration then spans the dropout,
        though its average still reflects only real samples (see
        ``_build_intervals``). Detection assumes a continuously recorded effort;
        this is a documented BETA limitation.
        """
        numpy = self._np
        filled = raw.copy()
        n = len(filled)
        last = float("nan")
        for i in range(n):
            if numpy.isnan(filled[i]):
                filled[i] = last
            else:
                last = float(filled[i])
        # Back-fill any leading NaN with the first finite value.
        finite = numpy.where(~numpy.isnan(filled))[0]
        if len(finite):
            first_value = filled[finite[0]]
            filled[: finite[0]] = first_value
        return filled

    def _smooth(self, values: np.ndarray) -> np.ndarray:
        """Return a centred moving average of ``values`` over ``SMOOTH_WINDOW_S``.

        A short centred average suppresses single-sample spikes before
        segmentation. The window is odd (so it is centred) and uses ``mode="same"``
        with edge padding, so the result is the same length as the input and the
        ends are not dragged towards zero.
        """
        numpy = self._np
        window = SMOOTH_WINDOW_S if SMOOTH_WINDOW_S % 2 == 1 else SMOOTH_WINDOW_S + 1
        if len(values) < window:
            return values
        half = window // 2
        padded = numpy.pad(values, half, mode="edge")
        kernel = numpy.ones(window) / window
        return numpy.convolve(padded, kernel, mode="valid")

    def _segment(
        self, smoothed: np.ndarray, delta_min: float, min_seconds: float
    ) -> list[tuple[int, int]]:
        """Segment the smoothed trace into piecewise-constant steps.

        A mean-shift contiguous pass produces the initial steps, then two
        post-processing passes that are where the ride-average scaling bites:

        1. Merge adjacent segments whose means differ by less than ``delta_min``.
           This collapses within-block jitter into one effort and is what stops a
           hard ride fragmenting into dozens of laps.
        2. Absorb every segment shorter than ``min_seconds`` into a neighbour.
           ``min_seconds`` is the shortest interval worth reporting, so this is the
           knob that genuinely coarsens the overlay: at the 10 s default a 20 s
           sprint survives and a 4 s blip is merged; raise it and short reps fold
           away too.

        Returns
        -------
        list of (int, int)
            ``(start_index, end_index)`` half-open spans over the grid, contiguous
            and covering the whole trace.
        """
        n = len(smoothed)
        if n == 0:
            return []
        bounds = self._mean_shift(smoothed, delta_min)
        bounds = self._merge_similar(bounds, smoothed, delta_min)
        bounds = self._absorb_short(bounds, smoothed, min_seconds)
        bounds = self._merge_similar(bounds, smoothed, delta_min)
        return bounds

    def _mean_shift(
        self, smoothed: np.ndarray, delta_min: float
    ) -> list[tuple[int, int]]:
        """Group the trace into contiguous runs that each hold one effort level.

        Walks the smoothed trace once, keeping a running mean of the current run.
        A sample joins the run while it stays within ``delta_min`` of that running
        mean; the first sample that departs by ``delta_min`` closes the run and
        opens a new one. This is the cheap, numpy-only alternative to a
        change-point library, and it has a property a naive per-sample diff lacks:
        the gradual ramp the smoothing leaves at a sharp step is *absorbed* into
        whichever block it is still close to, rather than fragmenting into one
        segment per ramp sample. Only when the cumulative drift reaches
        ``delta_min`` does a new effort begin, exactly the "sustained change of
        at least delta_min" the design calls for.

        Returns the half-open ``(start, end)`` spans, contiguous over the trace.
        """
        n = len(smoothed)
        bounds: list[tuple[int, int]] = []
        start = 0
        run_sum = float(smoothed[0])
        run_count = 1
        for i in range(1, n):
            value = float(smoothed[i])
            run_mean = run_sum / run_count
            if abs(value - run_mean) >= delta_min:
                bounds.append((start, i))
                start = i
                run_sum = value
                run_count = 1
            else:
                run_sum += value
                run_count += 1
        bounds.append((start, n))
        return bounds

    def _seg_mean(self, smoothed: np.ndarray, span: tuple[int, int]) -> float:
        """Return the mean of the smoothed trace over a half-open span."""
        return float(self._np.mean(smoothed[span[0] : span[1]]))

    def _merge_similar(
        self, bounds: list[tuple[int, int]], smoothed: np.ndarray, delta_min: float
    ) -> list[tuple[int, int]]:
        """Merge adjacent segments whose means differ by less than ``delta_min``.

        Repeated until stable, so a chain of small steps that each sit within
        ``delta_min`` of their neighbour collapses into the one effort they really
        are. This is the pass that the ride-average scaling drives: on a
        high-average ride a +30 W wobble is below ``delta_min`` and merges away; on
        a low-average ride the same wobble clears ``delta_min`` and is kept.
        """
        changed = True
        while changed and len(bounds) > 1:
            changed = False
            merged: list[tuple[int, int]] = [bounds[0]]
            for span in bounds[1:]:
                prev = merged[-1]
                if (
                    abs(self._seg_mean(smoothed, prev) - self._seg_mean(smoothed, span))
                    < delta_min
                ):
                    merged[-1] = (prev[0], span[1])
                    changed = True
                else:
                    merged.append(span)
            bounds = merged
        return bounds

    def _absorb_short(
        self,
        bounds: list[tuple[int, int]],
        smoothed: np.ndarray,
        min_seconds: float,
    ) -> list[tuple[int, int]]:
        """Absorb every segment shorter than ``min_seconds`` into a neighbour.

        ``min_seconds`` is the shortest interval worth reporting: a shorter segment
        is folded into whichever neighbour it is closer in mean to. Raising the
        knob therefore genuinely coarsens the overlay (it is the one lever a user
        turns for fewer, longer intervals), while the default (10 s) still keeps
        short reps: a 20 s sprint survives, a 4 s blip is merged. Walks left to
        right, re-evaluating after each fold so a string of tiny blips collapses
        cleanly.
        """
        min_samples = min_seconds * GRID_HZ
        changed = True
        while changed and len(bounds) > 1:
            changed = False
            for i, span in enumerate(bounds):
                if span[1] - span[0] >= min_samples:
                    continue
                bounds = self._fold_into_neighbour(bounds, i, smoothed)
                changed = True
                break
        return bounds

    def _fold_into_neighbour(
        self, bounds: list[tuple[int, int]], i: int, smoothed: np.ndarray
    ) -> list[tuple[int, int]]:
        """Merge segment ``i`` into its nearer-in-mean neighbour, returning new bounds."""
        here = self._seg_mean(smoothed, bounds[i])
        fold_left = i > 0 and (
            i == len(bounds) - 1
            or abs(here - self._seg_mean(smoothed, bounds[i - 1]))
            <= abs(here - self._seg_mean(smoothed, bounds[i + 1]))
        )
        result = list(bounds)
        if fold_left:
            result[i - 1 : i + 1] = [(bounds[i - 1][0], bounds[i][1])]
        else:
            result[i : i + 2] = [(bounds[i][0], bounds[i + 1][1])]
        return result

    def _build_intervals(
        self, times: list[datetime], raw: np.ndarray, bounds: list[tuple[int, int]]
    ) -> list[Interval]:
        """Turn segment spans into :class:`Interval` steps with raw-sample averages.

        Each interval's average is the mean of the *raw* (pre-smoothing) samples in
        its span: the smoothing was only ever to find the boundaries, so the
        reported number reflects the real recorded effort. A span whose raw samples
        are all ``NaN`` (a gap) falls back to ``0.0`` rather than producing ``NaN``.
        """
        numpy = self._np
        intervals: list[Interval] = []
        last_index = len(times) - 1
        for start, end in bounds:
            # The span is half-open over grid indices; the interval's end time is
            # the last grid point it covers (end - 1), so consecutive intervals
            # abut.
            end_index = min(end, last_index)
            segment = raw[start:end]
            with _suppress_nan_warning(numpy):
                mean = float(numpy.nanmean(segment)) if len(segment) else 0.0
            if mean != mean:  # NaN: an all-gap span
                mean = 0.0
            start_time = times[start]
            end_time = times[end_index]
            intervals.append(
                Interval(
                    start_time=start_time,
                    end_time=end_time,
                    average=mean,
                    duration_seconds=(end_time - start_time).total_seconds(),
                )
            )
        return intervals


def _load_numpy() -> Any:
    """Import the optional ``numpy`` dependency, or raise a helpful error.

    numpy is an optional extra (``pip install 'gpsmerge[workout]'``); the core
    install does without it. The detector imports it only when actually run, and
    turns a missing import into an actionable :class:`RuntimeError` rather than
    letting an opaque ``ImportError`` surface, mirroring how
    :func:`gpsmerge.io.fit._load_fit_tool` defers fit-tool.
    """
    try:
        import numpy

        return numpy
    except ImportError as exc:  # pragma: no cover - exercised when extra is absent
        raise RuntimeError(
            "detecting a workout needs the optional 'numpy' dependency, which is "
            "not installed. Install it with: pip install 'gpsmerge[workout]' "
            "(or: uv sync --extra workout)"
        ) from exc


def _select_channel(activity: Activity) -> str:
    """Choose the effort channel automatically: power for rides, else speed.

    Power wins when the activity carries a usable power trace (a ride); otherwise
    speed (a run, paced by speed). "Usable" is about *effort*, not variation: a
    steady trainer block is one held effort and still a workout. If neither
    channel is usable, there is nothing to detect, so a
    :class:`~gpsmerge.errors.WorkoutError` is raised. There is no flag to force a
    channel, by design.
    """
    if _channel_usable(activity, "power", FLOOR_POWER):
        return "power"
    if _channel_usable(activity, "speed", FLOOR_SPEED):
        return "speed"
    raise WorkoutError(
        "no usable power or speed data to detect a workout from: the activity "
        "has neither a usable power channel (for rides) nor a usable speed "
        "channel (for runs)"
    )


def _channel_usable(activity: Activity, channel: str, floor: float) -> bool:
    """Return whether a channel carries real, segmentable effort.

    The test is on MAGNITUDE, not variation: the channel's 95th-percentile value
    must clear the per-channel floor. Testing magnitude (rather than ``max - min``
    spread) is what handles both ends honestly. A steady effort is accepted: a
    constant trainer block is one valid lap, not "no data". A dead channel is
    rejected even when it glitches: a disconnected power meter reading a flat ``0``
    with a few stray spike frames has a 95th percentile of ``0`` (a single outlier
    cannot move a percentile), so it is skipped and a real speed trace is used
    instead, rather than reporting a run as ``"0 W"``.
    """
    values = [
        float(getattr(p, channel))
        for p in activity.points()
        if getattr(p, channel) is not None
    ]
    if len(values) < 2:
        return False
    return _percentile(sorted(values), 95.0) >= floor


def _percentile(ordered: list[float], pct: float) -> float:
    """Return the ``pct`` percentile of a pre-sorted list by linear interpolation."""
    if not ordered:
        return 0.0
    rank = (pct / 100.0) * (len(ordered) - 1)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def _floor(channel: str) -> float:
    """Return the absolute significance floor for a channel, in its native units."""
    return FLOOR_POWER if channel == "power" else FLOOR_SPEED


def _build_grid(
    activity: Activity, channel: str
) -> tuple[list[datetime], list[float | None]]:
    """Resample a channel onto a 1 Hz grid over the activity's timed span.

    The recorded ``(time, value)`` samples are interpolated with
    :func:`gpsmerge.util.resample.lerp` onto one-second steps from the first to the
    last sample. A grid point inside a recording gap longer than the smoothing
    window comes back ``None`` (lerp blanks wide brackets), so genuine dropouts
    are not invented across.

    Returns
    -------
    tuple
        ``(times, values)``: the tz-aware UTC time of each grid point and its
        interpolated value (``None`` where there is no nearby data).
    """
    samples = activity.channel_series(channel)
    if not samples:
        # _select_channel guarantees a usable channel, so this never fires in a
        # normal run; the guard keeps the helper total for a direct caller.
        return [], []
    start_s = samples[0][0]
    end_s = samples[-1][0]
    step = 1.0 / GRID_HZ
    n_steps = int(round((end_s - start_s) / step))
    grid_s = [start_s + i * step for i in range(n_steps + 1)]
    # Allow lerp to bridge gaps up to the smoothing window; anything wider blanks
    # to None and is treated as a real dropout.
    values = lerp(grid_s, samples, max_gap_s=float(SMOOTH_WINDOW_S))
    times = [_from_seconds(s) for s in grid_s]
    return times, values


def _from_seconds(seconds: float) -> datetime:
    """Return a tz-aware UTC datetime for POSIX ``seconds``."""
    from datetime import UTC

    return datetime.fromtimestamp(seconds, tz=UTC)


class _suppress_nan_warning:
    """Context manager silencing numpy's empty-slice / all-NaN runtime warning."""

    def __init__(self, numpy: Any) -> None:
        self._numpy = numpy
        self._ctx: Any = None

    def __enter__(self) -> None:
        self._ctx = self._numpy.errstate(invalid="ignore")
        self._ctx.__enter__()

    def __exit__(self, *exc: object) -> None:
        self._ctx.__exit__(*exc)


# --- summary rendering -------------------------------------------------------


def _summarise(intervals: list[Interval], channel: str) -> str:
    """Render the intervals as a comma-separated human-readable summary line."""
    return ", ".join(_render_interval(iv, channel) for iv in intervals)


def _render_interval(interval: Interval, channel: str) -> str:
    """Render one interval as ``M:SS @ <effort>``.

    Power renders the average rounded to the nearest 5 W (``"5:00 @ 300 W"``);
    speed renders the average as a pace per kilometre (``"3:00 @ 4:15/km"``).
    """
    duration = _format_mmss(interval.duration_seconds)
    if channel == "power":
        return f"{duration} @ {_round_to_5(interval.average)} W"
    return f"{duration} @ {_format_pace(interval.average)}/km"


def _format_mmss(seconds: float) -> str:
    """Format a duration as ``M:SS`` (minutes can exceed 59, e.g. ``75:00``)."""
    total = int(round(seconds))
    minutes, secs = divmod(total, 60)
    return f"{minutes}:{secs:02d}"


def _round_to_5(value: float) -> int:
    """Round a wattage to the nearest 5 W."""
    return int(round(value / 5.0) * 5)


def _format_pace(speed_mps: float) -> str:
    """Format a speed in m/s as a ``P:PP/km`` pace string.

    Pace is ``1000 / speed`` seconds per kilometre. A non-positive speed has no
    meaningful pace (you were not moving), so it renders as ``"--:--"`` rather
    than dividing by zero.
    """
    if speed_mps <= 0:
        return "--:--"
    seconds_per_km = 1000.0 / speed_mps
    return _format_mmss(seconds_per_km)
