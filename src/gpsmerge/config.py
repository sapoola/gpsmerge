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
    gap_policy: Literal["pause", "shift"] = "pause"
    ignore_big_gaps_m: float | None = None
    dedupe: bool = True
    recompute_distance: bool = True
    device_from: Path | None = None
    sport: str | None = None
    assume_yes: bool = False
    dry_run: bool = False
    json_output: bool = False
