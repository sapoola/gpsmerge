"""The settings object that describes one merge.

Everything the wizard collects or the command-line flags set ends up in a single
:class:`Settings` instance, which the pipeline reads. Keeping one validated
object means the interactive and scripted paths share exactly the same inputs,
and there is one obvious place to see every option a merge takes.

The model grows with the tool. Today it carries what appending needs; stacking
options arrive with stacking.
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
    output_format : {"gpx", "tcx"}
        The format to write.
    mode : {"auto", "append", "stack"}
        How to combine the files. ``"append"`` joins sequential recordings;
        ``"stack"`` overlays recordings of the same ride, taking a different
        sensor from each; ``"auto"`` decides from whether the files overlap.
    max_gap_s : float
        When stacking, do not interpolate a borrowed channel across a gap in its
        source longer than this many seconds (leave it blank instead).
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
    """

    inputs: list[Path]
    output: Path
    output_format: Literal["gpx", "tcx"] = "gpx"
    mode: Literal["auto", "append", "stack"] = "auto"
    max_gap_s: float = 10.0
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
