"""The settings object that describes one merge.

Everything the wizard collects or the command-line flags set ends up in a single
:class:`Settings` instance, which the pipeline reads, so the interactive and
scripted paths share exactly the same inputs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel


class Settings(BaseModel):
    """All the inputs a single merge needs.

    Attributes
    ----------
    inputs : list of Path
        The files to merge, in the order the user gave them.
    output : Path
        Where to write the merged file.
    output_format : {"gpx", "tcx", "fit"}
        The format to write. ``"fit"`` is experimental and needs the optional
        ``fit-tool`` extra.
    mode : {"auto", "append", "stack"}
        How to combine the files. ``"append"`` joins sequential recordings;
        ``"stack"`` overlays recordings of the same ride, taking a different
        sensor from each; ``"auto"`` decides from whether the files overlap.
    max_gap_s : float
        When stacking, do not interpolate a borrowed channel across a gap in its
        source longer than this many seconds (leave it blank instead).
    clock_align : bool
        When stacking, detect and correct a donor device's clock offset before
        borrowing its channels.
    position_from : Path or None
        When stacking, which input provides position (and elevation). Defaults
        to the input with the best GPS track.
    hr_from, cadence_from, power_from, temp_from : Path or None
        When stacking, which input provides each sensor channel. Defaults to the
        inputs that recorded it, in order, so a second source fills the first's
        gaps.
    gap_policy : {"pause", "shift"}
        How to treat the time gap between two appended recordings. ``"pause"``
        keeps the real timestamps so the gap shows as stopped time; ``"shift"``
        moves the later recording to start right after the earlier one.
    ignore_big_gaps_m : float or None
        If set, a jump larger than this many metres between consecutive points
        is not counted towards distance (a coffee stop or a GPS glitch should
        not inflate the total).
    dedupe : bool
        Drop a duplicated point at the seam where two recordings meet.
    recompute_distance : bool
        Recompute cumulative distance from positions after merging.
    device_from : Path or None
        Which input file's recording device the merged file is attributed to.
        Defaults to the first input.
    sport : str or None
        Override the sport of the merged activity; otherwise the first file's.
    assume_yes : bool
        Overwrite the output without asking.
    dry_run : bool
        Do everything except write the file; show the preview only.
    json_output : bool
        Print a machine-readable summary instead of the human preview.
    timezone : str or None
        The raw timezone spec (an IANA name such as ``"Europe/Amsterdam"`` or a
        fixed offset such as ``"+02:00"``). It makes the preview and JSON show
        local times so the user can sanity-check, and a FIT output records it as
        the activity's local time (what Strava reads). The merged file's
        timestamps always stay UTC.
    shift : str or None
        A signed duration to add to every timestamp after merging, to correct a
        wrong device clock (e.g. ``"+1h"``, ``"-30m"``, ``"+1:30:00"``). Kept as
        the raw spec so validation can report a clean parse error; see
        :func:`gpsmerge.util.timeutil.parse_duration`.
    trim_start, trim_end : str or None
        ISO timestamps bounding which points to keep after merging; an open bound
        is unbounded on that side. Kept raw so a bad value is a clean validation
        error rather than a crash; see :func:`gpsmerge.util.timeutil.parse_time`.
    smooth_elevation : int or None
        If set, the window width for the centred moving average that smooths
        elevation. Must be an odd integer at least 3 (validation enforces this).
    insert_stopped_time : bool
        Split the merged track at every long non-moving gap so the gap counts as
        a pause (stopped time) rather than moving time.
    stopped_time_threshold_s : float
        When ``insert_stopped_time`` is set, a gap longer than this many seconds
        becomes a segment boundary.
    auto_lap_m : float or None
        If set, re-lap the merged activity into even distance splits of this many
        metres (a lap every 1 km, say), replacing any source laps. Parsed from a
        spec like ``"1km"`` or ``"0.5mi"`` by :func:`gpsmerge.util.geo.parse_distance`
        and stored as metres. ``None`` leaves the source laps untouched.
    detect_workout : bool
        Auto-detect a structured interval workout from the merged activity's
        primary effort channel (power for rides, speed for runs, chosen
        automatically) and express it as laps plus a summary line. BETA, a
        best-effort heuristic. Needs the optional ``workout`` extra (numpy).
        Mutually exclusive with ``auto_lap_m`` (both set the laps), which
        validation enforces.
    workout_min_seconds : float
        When detecting a workout, segments shorter than this many seconds are
        absorbed into a neighbour unless they are a real, distinct short effort.
        Default ``10``.
    """

    inputs: list[Path]
    output: Path
    output_format: Literal["gpx", "tcx", "fit"] = "gpx"
    mode: Literal["auto", "append", "stack"] = "auto"
    max_gap_s: float = 10.0
    clock_align: bool = False
    position_from: Path | None = None
    hr_from: Path | None = None
    cadence_from: Path | None = None
    power_from: Path | None = None
    temp_from: Path | None = None
    gap_policy: Literal["pause", "shift"] = "pause"
    ignore_big_gaps_m: float | None = None
    dedupe: bool = True
    recompute_distance: bool = True
    device_from: Path | None = None
    sport: str | None = None
    assume_yes: bool = False
    dry_run: bool = False
    json_output: bool = False
    timezone: str | None = None
    shift: str | None = None
    trim_start: str | None = None
    trim_end: str | None = None
    smooth_elevation: int | None = None
    insert_stopped_time: bool = False
    stopped_time_threshold_s: float = 30.0
    auto_lap_m: float | None = None
    detect_workout: bool = False
    workout_min_seconds: float = 10.0
