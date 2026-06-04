"""Typed errors and the :class:`Issue` type used to report problems to the user.

An :class:`Issue` is a problem we can explain and recover from (the wrong number
of files, overlapping inputs, an output path we cannot write). Each one carries
the process exit code it should produce, so the same value drives both the
message a person reads and the status code a script checks. The ``EXIT_*``
constants are the whole taxonomy.
"""

from __future__ import annotations

from dataclasses import dataclass

# Process exit codes. 0 is success; the rest mirror the categories of failure a
# caller (a person or a script) needs to tell apart.
EXIT_OK = 0
EXIT_USAGE = 2  # wrong arguments (too few files, bad flag combination)
EXIT_PARSE = 3  # an input file could not be read
EXIT_MODE_CONFLICT = 4  # the files do not match the chosen mode (e.g. they overlap)
EXIT_EMPTY_RESULT = 5  # the merge produced nothing usable
EXIT_WRITE = 6  # the output could not be written
EXIT_NO_WORKOUT_DATA = 7  # --detect-workout found no usable power or speed channel


@dataclass(slots=True)
class Issue:
    """A blocking problem, with the exit code it should produce.

    Attributes
    ----------
    code : int
        One of the ``EXIT_*`` constants; becomes the process exit code.
    message : str
        A human-readable explanation of what is wrong and how to fix it.
    """

    code: int
    message: str


class GpsmergeError(Exception):
    """Base class for errors raised by gpsmerge."""


class ParseError(GpsmergeError):
    """Raised when an input file cannot be read or understood."""


class UnsupportedFormat(GpsmergeError):
    """Raised for a file extension or output format we do not handle yet."""


class WriteError(GpsmergeError):
    """Raised when an activity cannot be serialised to the requested format.

    The merge itself succeeded, but the chosen output format cannot represent
    some value -- for example a sensor reading outside the range FIT can store.
    Belongs to the :data:`EXIT_WRITE` category.
    """


class TransformError(GpsmergeError):
    """Raised when a post-merge transform cannot be applied to an activity.

    The merge succeeded, but the requested adjustment makes no sense for this
    data -- for example asking to re-lap by distance an activity that recorded
    neither a distance channel nor any positions to measure one from.
    Validation surfaces such a case as an :class:`Issue` up front, so this is a
    last-line guard for a direct caller rather than something a normal run hits.
    """


class WorkoutError(GpsmergeError):
    """Raised when ``--detect-workout`` has no usable effort channel to work from.

    The workout detector needs a varying power channel (rides) or a varying
    speed channel (runs) to find intervals in. An activity with neither -- or
    one where the chosen channel has too little signal to trust -- cannot yield
    a workout, so this is raised with a clear, actionable message. The pipeline
    maps it to :data:`EXIT_NO_WORKOUT_DATA`.
    """
