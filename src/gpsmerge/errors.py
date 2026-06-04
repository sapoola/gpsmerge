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
