"""gpsmerge — an offline, local-first merger for GPX, TCX, and FIT activity files."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    # Single source of truth is pyproject's [project] version; read it from the
    # installed distribution metadata so the two can never drift.
    __version__ = version("gpsmerge")
except PackageNotFoundError:
    # Running from a source tree that was never installed (e.g. a bare checkout).
    __version__ = "0.0.0"
