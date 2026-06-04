"""Write an :class:`~gpsmerge.model.Activity` to a file in a chosen format.

The mirror of :mod:`gpsmerge.read`: one place that maps an output format name to
a writer. Adding a format is one ``if`` branch plus its ``write_<format>``
function.
"""

from __future__ import annotations

from pathlib import Path

from gpsmerge.errors import UnsupportedFormat
from gpsmerge.gpx import write_gpx
from gpsmerge.model import Activity


def write(activity: Activity, path: Path, output_format: str) -> None:
    """Write an activity to ``path`` in ``output_format``.

    Parameters
    ----------
    activity : Activity
        The activity to serialise.
    path : Path
        Destination file path; overwritten if it exists.
    output_format : str
        The format to write. Today only ``"gpx"`` is supported; ``"tcx"``
        arrives in a later milestone.

    Raises
    ------
    UnsupportedFormat
        If ``output_format`` is not one we write.
    """
    if output_format == "gpx":
        write_gpx(activity, path)
        return
    raise UnsupportedFormat(f"cannot write {output_format!r} yet")
