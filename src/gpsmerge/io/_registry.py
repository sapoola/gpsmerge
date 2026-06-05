"""The format table that read/write dispatch is built on.

Each supported format is one :class:`Format` row pairing a file suffix and an
output-format name with the functions that parse and serialise it. Collecting
them here gives a single source of truth: :mod:`gpsmerge.io.read` keys on
``suffix``, :mod:`gpsmerge.io.write` keys on ``output_format``, and
``READABLE_SUFFIXES`` is derived from the table rather than hand-maintained.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from gpsmerge.io.fit import read_fit, write_fit
from gpsmerge.io.gpx import read_gpx, write_gpx
from gpsmerge.io.tcx import read_tcx, write_tcx
from gpsmerge.model import Activity


@dataclass(frozen=True)
class Format:
    """One supported file format: its suffix, output name, and read/write functions."""

    suffix: str
    output_format: str
    read: Callable[[Path], Activity]
    write: Callable[[Activity, Path], None]


# The supported formats, in a fixed order so READABLE_SUFFIXES stays stable.
_FORMATS: tuple[Format, ...] = (
    Format(".gpx", "gpx", read_gpx, write_gpx),
    Format(".tcx", "tcx", read_tcx, write_tcx),
    Format(".fit", "fit", read_fit, write_fit),
)
_BY_SUFFIX: dict[str, Format] = {fmt.suffix: fmt for fmt in _FORMATS}
_BY_FORMAT: dict[str, Format] = {fmt.output_format: fmt for fmt in _FORMATS}

# The file extensions read() understands, derived from the table so the wizard's
# file validation and prompt text share one source of truth.
READABLE_SUFFIXES: tuple[str, ...] = tuple(_BY_SUFFIX)
