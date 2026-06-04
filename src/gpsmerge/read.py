"""Read any supported file into an :class:`~gpsmerge.model.Activity`.

This is the one place that maps a file extension to a reader. Adding a format is
adding one ``if`` branch here plus its ``read_<format>`` function -- there is no
registry and no base class to subclass.
"""

from __future__ import annotations

from pathlib import Path

from gpsmerge.errors import ParseError, UnsupportedFormat
from gpsmerge.gpx import read_gpx
from gpsmerge.model import Activity


def read(path: Path) -> Activity:
    """Read a GPS file into the canonical :class:`~gpsmerge.model.Activity`.

    Parameters
    ----------
    path : Path
        Path to a supported file. Today that means ``.gpx``; ``.tcx`` and
        ``.fit`` arrive in later milestones.

    Returns
    -------
    Activity
        The parsed activity.

    Raises
    ------
    UnsupportedFormat
        If the file's extension is not one we read.
    ParseError
        If the file exists but cannot be read or parsed.
    """
    ext = path.suffix.lower()
    if ext != ".gpx":
        raise UnsupportedFormat(
            f"cannot read {path.name}: {ext or 'no extension'} is not supported yet"
        )
    # Boundary: turn any IO or XML-parse failure into one typed error the
    # pipeline can report as a parse problem.
    try:
        return read_gpx(path)
    except Exception as exc:
        raise ParseError(f"could not read {path.name}: {exc}") from exc
