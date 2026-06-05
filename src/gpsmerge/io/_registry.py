"""The codec registry that read/write dispatch is built on.

Each supported format contributes one codec (an object with a ``suffix``, an
``output_format``, and ``read``/``write`` methods). Collecting them here gives a
single source of truth: :mod:`gpsmerge.io.read` keys on ``suffix``,
:mod:`gpsmerge.io.write` keys on ``output_format``, and ``READABLE_SUFFIXES`` is
derived from the registry rather than hand-maintained.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from gpsmerge.io.fit import FitCodec
from gpsmerge.io.gpx import GpxCodec
from gpsmerge.io.tcx import TcxCodec
from gpsmerge.model import Activity


class Codec(Protocol):
    """A single-format reader/writer the registry dispatches on.

    Structural, not a base class: any object with these four members is a codec.
    ``suffix`` is the read key (e.g. ``".gpx"``) and ``output_format`` the write
    key (e.g. ``"gpx"``).
    """

    suffix: str
    output_format: str

    def read(self, path: Path) -> Activity: ...

    def write(self, activity: Activity, path: Path) -> None: ...


# The registered codecs, in a fixed order so READABLE_SUFFIXES stays stable.
_CODECS: tuple[Codec, ...] = (GpxCodec(), TcxCodec(), FitCodec())
_BY_SUFFIX: dict[str, Codec] = {codec.suffix: codec for codec in _CODECS}
_BY_FORMAT: dict[str, Codec] = {codec.output_format: codec for codec in _CODECS}

# The file extensions read() understands, derived from the registry so the
# wizard's file validation and prompt text share one source of truth.
READABLE_SUFFIXES: tuple[str, ...] = tuple(_BY_SUFFIX)
