"""Read any supported file into an :class:`~gpsmerge.model.Activity`.

The format table maps a file extension to the function that parses it, so this
stays the one place a read is dispatched.
"""

from __future__ import annotations

from pathlib import Path

from gpsmerge.errors import ParseError, UnsupportedFormat
from gpsmerge.io._registry import _BY_SUFFIX, READABLE_SUFFIXES
from gpsmerge.model import Activity

__all__ = ["READABLE_SUFFIXES", "read"]


def read(path: Path) -> Activity:
    """Read a GPS file into the canonical :class:`~gpsmerge.model.Activity`.

    Parameters
    ----------
    path : Path
        Path to a supported file. Today that means ``.gpx``, ``.tcx``, or
        ``.fit``.

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
    fmt = _BY_SUFFIX.get(ext)
    if fmt is None:
        raise UnsupportedFormat(
            f"cannot read {path.name}: {ext or 'no extension'} is not supported yet"
        )
    # Boundary: turn any IO or parse failure into one typed error the pipeline
    # can report as a parse problem.
    try:
        return fmt.read(path)
    except Exception as exc:
        raise ParseError(f"could not read {path.name}: {exc}") from exc
