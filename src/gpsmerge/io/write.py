"""Write an :class:`~gpsmerge.model.Activity` to a file in a chosen format.

The mirror of :mod:`gpsmerge.io.read`: one place that maps an output format name to
a writer. Adding a format is one ``if`` branch plus its ``write_<format>``
function.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from tempfile import mkstemp

from gpsmerge.errors import UnsupportedFormat
from gpsmerge.io.fit import write_fit
from gpsmerge.io.gpx import write_gpx
from gpsmerge.io.tcx import write_tcx
from gpsmerge.model import Activity

# Maps an output format to the writer that serialises an Activity to a path.
# ``fit`` is experimental and needs the optional ``fit-tool`` extra; write_fit
# raises a clear install hint when it is missing, so registering it here is safe
# even without the extra installed.
_WRITERS: dict[str, Callable[[Activity, Path], None]] = {
    "gpx": write_gpx,
    "tcx": write_tcx,
    "fit": write_fit,
}


def write(activity: Activity, path: Path, output_format: str) -> None:
    """Write an activity to ``path`` in ``output_format``, atomically.

    The activity is serialised to a temporary file in the *same* directory as
    ``path`` and then :func:`os.replace`-d onto it, which is atomic on a single
    filesystem. A crash or error part-way through therefore leaves any existing
    ``path`` untouched rather than half-overwritten -- the destination either holds
    the previous file or the complete new one, never a truncated mix. The same
    directory matters: a cross-filesystem replace is not atomic, so a temp file in
    the system temp dir would defeat the guarantee.

    Parameters
    ----------
    activity : Activity
        The activity to serialise.
    path : Path
        Destination file path; overwritten (atomically) if it exists.
    output_format : str
        The format to write: ``"gpx"``, ``"tcx"``, or (experimental) ``"fit"``.
        Writing FIT needs the optional ``fit-tool`` extra; without it the FIT
        writer raises a clear install hint.

    Raises
    ------
    UnsupportedFormat
        If ``output_format`` is not one we write.
    """
    writer = _WRITERS.get(output_format)
    if writer is None:
        raise UnsupportedFormat(f"cannot write {output_format!r} yet")
    _write_atomic(writer, activity, path)


def _write_atomic(
    writer: Callable[[Activity, Path], None], activity: Activity, path: Path
) -> None:
    """Serialise via ``writer`` to a sibling temp file, then replace ``path``.

    The temp file is created in ``path``'s parent so the final
    :func:`os.replace` stays on one filesystem and is atomic. On any failure the
    temp file is removed and the original ``path`` is left intact.
    """
    fd, temp_name = mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    os.close(fd)  # the writers open the path themselves; we only needed the name.
    temp_path = Path(temp_name)
    try:
        writer(activity, temp_path)
        os.replace(temp_path, path)
    except BaseException:
        # Leave the destination as it was; never strand the temp file.
        temp_path.unlink(missing_ok=True)
        raise
