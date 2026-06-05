"""Saved, file-independent merge settings the user can name and reuse.

A *profile* is a small TOML file holding the general options of a merge -- the
ones that do not depend on which files you happen to be merging this time, such
as the mode, the output format, and the timezone. The motivating case is a user
who merges a trainer FIT and a watch TCX the same way every week: saving those
choices once as ``trainer`` turns a long command into ``--profile trainer``.

Profiles live under the platform config dir (``profiles_dir``), one ``.toml``
file per profile. Only the keys in :data:`SAVEABLE_KEYS` are ever stored, and
only when their value is not ``None`` -- TOML has no null, and a ``None`` simply
means "fall back to the default", which a saved profile should not pin.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING

import tomli_w
from platformdirs import user_config_dir

if TYPE_CHECKING:
    from gpsmerge.config import Settings

# The general, file-independent settings a profile may store. Everything else on
# Settings is either file-specific (the inputs, the per-channel sources, the
# device) or per-run (output path, assume_yes, dry_run, json_output) and must
# never be baked into a reusable profile.
SAVEABLE_KEYS: tuple[str, ...] = (
    "mode",
    "output_format",
    "max_gap_s",
    "gap_policy",
    "ignore_big_gaps_m",
    "dedupe",
    "recompute_distance",
    "sport",
    "timezone",
)

# A safe profile name is a single path segment: letters, digits, and a few
# punctuation marks, with no separator, no parent reference, and not empty. This
# keeps ``<name>.toml`` inside the profiles directory (no path traversal).
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]*$")

_PROFILE_SUFFIX = ".toml"


def profiles_dir() -> Path:
    """Return the directory profiles are stored in.

    Returns
    -------
    Path
        ``<user config dir>/gpsmerge/profiles``. The directory may not exist
        yet; callers that write create it.
    """
    return Path(user_config_dir("gpsmerge")) / "profiles"


def validate_name(name: str) -> None:
    """Reject a profile name that is not a single safe filename segment.

    Public so a caller can check a name up front (e.g. ``--save-profile`` before
    running a merge) and fail fast rather than as a traceback later.

    Parameters
    ----------
    name : str
        The candidate profile name.

    Raises
    ------
    ValueError
        If `name` is empty, contains a path separator or ``..``, or otherwise is
        not a simple filename. This is what blocks path traversal.
    """
    if not name or "/" in name or "\\" in name or ".." in name:
        raise ValueError(f"invalid profile name: {name!r}")
    if not _SAFE_NAME.match(name):
        raise ValueError(f"invalid profile name: {name!r}")


def _profile_path(name: str, base: Path | None) -> Path:
    """Return the file path for a named profile under `base` (or the default)."""
    validate_name(name)
    root = base if base is not None else profiles_dir()
    return root / f"{name}{_PROFILE_SUFFIX}"


def save_profile(name: str, data: dict[str, object], base: Path | None = None) -> Path:
    """Write a profile's settings to ``base/<name>.toml``.

    Parameters
    ----------
    name : str
        The profile name (a safe single filename segment).
    data : dict
        The saveable settings to store, already filtered to non-``None`` values.
    base : Path or None, optional
        The directory to write into; defaults to :func:`profiles_dir`. Created if
        it does not exist.

    Returns
    -------
    Path
        The path of the written profile file.

    Raises
    ------
    ValueError
        If `name` is not a safe filename.
    """
    path = _profile_path(name, base)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        tomli_w.dump(data, handle)
    return path


def load_profile(name: str, base: Path | None = None) -> dict:
    """Read a saved profile's settings.

    Parameters
    ----------
    name : str
        The profile name to load.
    base : Path or None, optional
        The directory to read from; defaults to :func:`profiles_dir`.

    Returns
    -------
    dict
        The saved key/value settings.

    Raises
    ------
    ValueError
        If `name` is not a safe filename, or no such profile exists (the message
        lists the profiles that are available).
    """
    path = _profile_path(name, base)
    if not path.is_file():
        available = list_profiles(base)
        choices = ", ".join(available) if available else "none"
        raise ValueError(f"no profile named {name!r} (available: {choices})")
    with path.open("rb") as handle:
        return tomllib.load(handle)


def list_profiles(base: Path | None = None) -> list[str]:
    """List the saved profile names, sorted.

    Parameters
    ----------
    base : Path or None, optional
        The directory to scan; defaults to :func:`profiles_dir`.

    Returns
    -------
    list of str
        The profile names (without the ``.toml`` suffix), sorted; an empty list
        if the directory does not exist.
    """
    root = base if base is not None else profiles_dir()
    if not root.is_dir():
        return []
    return sorted(path.stem for path in root.glob(f"*{_PROFILE_SUFFIX}"))


def delete_profile(name: str, base: Path | None = None) -> None:
    """Delete a saved profile.

    Parameters
    ----------
    name : str
        The profile name to delete.
    base : Path or None, optional
        The directory to delete from; defaults to :func:`profiles_dir`.

    Raises
    ------
    ValueError
        If `name` is not a safe filename, or no such profile exists.
    """
    path = _profile_path(name, base)
    if not path.is_file():
        raise ValueError(f"no profile named {name!r}")
    path.unlink()


def from_settings(settings: Settings) -> dict:
    """Extract the saveable, non-``None`` settings from a :class:`Settings`.

    Parameters
    ----------
    settings : Settings
        A fully built settings object.

    Returns
    -------
    dict
        The :data:`SAVEABLE_KEYS` whose value is not ``None``, ready to save.
        File-specific and per-run fields are never included.
    """
    return {
        key: getattr(settings, key)
        for key in SAVEABLE_KEYS
        if getattr(settings, key) is not None
    }
