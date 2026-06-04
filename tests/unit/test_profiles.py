"""Tests for saved config profiles and their command-line surface.

The module-level functions are exercised against a ``tmp_path`` base so nothing
touches the real config directory. The CLI tests drive the ``run`` command and
the ``profile`` sub-app through Typer's ``CliRunner``, monkeypatching
``profiles.profiles_dir`` to a temporary directory for the same reason.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from gpsmerge import profiles
from gpsmerge.cli import app
from gpsmerge.config import Settings
from gpsmerge.errors import EXIT_USAGE

runner = CliRunner()


# --- the profiles module -------------------------------------------------


@pytest.mark.unit
def test_save_then_load_round_trips_the_data(tmp_path: Path) -> None:
    data = {"output_format": "tcx", "mode": "stack", "timezone": "Europe/Amsterdam"}

    path = profiles.save_profile("trainer", data, base=tmp_path)
    loaded = profiles.load_profile("trainer", base=tmp_path)

    assert path == tmp_path / "trainer.toml"
    assert loaded == data


@pytest.mark.unit
def test_from_settings_keeps_only_saveable_non_none_keys() -> None:
    settings = Settings.model_validate(
        {
            "inputs": [Path("a.fit"), Path("b.tcx")],
            "output": Path("out.tcx"),
            "output_format": "tcx",
            "mode": "stack",
            "timezone": "Europe/Amsterdam",
            "device_from": Path("a.fit"),  # file-specific: must be dropped
            "sport": None,  # None: dropped even though saveable
            "assume_yes": True,  # per-run: must be dropped
        }
    )

    data = profiles.from_settings(settings)

    assert data["output_format"] == "tcx"
    assert data["mode"] == "stack"
    assert data["timezone"] == "Europe/Amsterdam"
    # Never the file-specific, per-run, or None-valued fields.
    for forbidden in ("inputs", "output", "device_from", "assume_yes", "sport"):
        assert forbidden not in data
    # Only saveable keys ever appear.
    assert set(data) <= set(profiles.SAVEABLE_KEYS)


@pytest.mark.unit
def test_from_settings_only_emits_saveable_keys() -> None:
    # A settings object with every saveable field set: the dict is exactly them.
    settings = Settings.model_validate(
        {
            "inputs": [Path("a.gpx"), Path("b.gpx")],
            "output": Path("out.gpx"),
            "mode": "append",
            "output_format": "gpx",
            "max_gap_s": 12.0,
            "gap_policy": "shift",
            "ignore_big_gaps_m": 250.0,
            "dedupe": False,
            "recompute_distance": False,
            "sport": "cycling",
            "timezone": "UTC",
        }
    )

    data = profiles.from_settings(settings)

    assert set(data) == set(profiles.SAVEABLE_KEYS)


@pytest.mark.unit
def test_list_profiles_returns_sorted_names(tmp_path: Path) -> None:
    profiles.save_profile("zulu", {"mode": "append"}, base=tmp_path)
    profiles.save_profile("alpha", {"mode": "stack"}, base=tmp_path)

    assert profiles.list_profiles(base=tmp_path) == ["alpha", "zulu"]


@pytest.mark.unit
def test_list_profiles_is_empty_when_dir_absent(tmp_path: Path) -> None:
    assert profiles.list_profiles(base=tmp_path / "missing") == []


@pytest.mark.unit
def test_delete_profile_removes_the_file(tmp_path: Path) -> None:
    profiles.save_profile("gone", {"mode": "append"}, base=tmp_path)

    profiles.delete_profile("gone", base=tmp_path)

    assert profiles.list_profiles(base=tmp_path) == []


@pytest.mark.unit
def test_delete_missing_profile_raises_value_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        profiles.delete_profile("nope", base=tmp_path)


@pytest.mark.unit
def test_load_missing_profile_lists_available_names(tmp_path: Path) -> None:
    profiles.save_profile("trainer", {"mode": "stack"}, base=tmp_path)

    with pytest.raises(ValueError) as excinfo:
        profiles.load_profile("commute", base=tmp_path)

    assert "trainer" in str(excinfo.value)


@pytest.mark.unit
@pytest.mark.parametrize("name", ["../x", "a/b", "", "..", "a\\b"])
def test_unsafe_name_raises_value_error(tmp_path: Path, name: str) -> None:
    with pytest.raises(ValueError):
        profiles.save_profile(name, {"mode": "append"}, base=tmp_path)


# --- the CLI surface -----------------------------------------------------

# A tiny two-point GPX; placeholders are filled per file.
_GPX_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<gpx creator="{creator}" version="1.1"
     xmlns="http://www.topografix.com/GPX/1/1">
 <trk>
  <type>cycling</type>
  <trkseg>
   <trkpt lat="{lat1}" lon="{lon1}">
    <ele>5.0</ele>
    <time>{time1}</time>
   </trkpt>
   <trkpt lat="{lat2}" lon="{lon2}">
    <ele>6.0</ele>
    <time>{time2}</time>
   </trkpt>
  </trkseg>
 </trk>
</gpx>
"""


def _two_sequential_files(tmp_path: Path) -> tuple[Path, Path]:
    """Write two non-overlapping GPX recordings five minutes apart."""
    a = tmp_path / "a.gpx"
    a.write_text(
        _GPX_TEMPLATE.format(
            creator="Garmin Edge 530",
            lat1=52.000,
            lon1=4.000,
            lat2=52.001,
            lon2=4.001,
            time1="2026-06-04T08:00:00Z",
            time2="2026-06-04T08:00:10Z",
        ),
        encoding="utf-8",
    )
    b = tmp_path / "b.gpx"
    b.write_text(
        _GPX_TEMPLATE.format(
            creator="Stages Dash M50",
            lat1=52.002,
            lon1=4.002,
            lat2=52.003,
            lon2=4.003,
            time1="2026-06-04T08:05:00Z",
            time2="2026-06-04T08:05:10Z",
        ),
        encoding="utf-8",
    )
    return a, b


@pytest.fixture()
def profile_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``profiles.profiles_dir`` at a temporary directory for a test."""
    home = tmp_path / "profiles"
    monkeypatch.setattr(profiles, "profiles_dir", lambda: home)
    return home


@pytest.mark.unit
def test_save_profile_flag_writes_the_general_settings(
    tmp_path: Path, profile_home: Path
) -> None:
    a, b = _two_sequential_files(tmp_path)
    output = tmp_path / "out.tcx"

    result = runner.invoke(
        app,
        [
            "run",
            str(a),
            str(b),
            "--out",
            str(output),
            "--format",
            "tcx",
            "--timezone",
            "Europe/Amsterdam",
            "--save-profile",
            "trainer",
        ],
    )

    assert result.exit_code == 0
    saved = profiles.load_profile("trainer", base=profile_home)
    assert saved["output_format"] == "tcx"
    assert saved["timezone"] == "Europe/Amsterdam"
    # The output path is per-run and must never be baked into the profile.
    assert "output" not in saved
    assert "saved profile 'trainer'" in result.output


@pytest.mark.unit
def test_save_profile_on_a_dry_run_still_saves(
    tmp_path: Path, profile_home: Path
) -> None:
    a, b = _two_sequential_files(tmp_path)
    output = tmp_path / "out.gpx"

    result = runner.invoke(
        app,
        [
            "run",
            str(a),
            str(b),
            "--out",
            str(output),
            "--dry-run",
            "--mode",
            "append",
            "--save-profile",
            "trainer",
        ],
    )

    assert result.exit_code == 0
    assert not output.exists()  # dry run wrote no file
    assert profiles.load_profile("trainer", base=profile_home)["mode"] == "append"


@pytest.mark.unit
def test_failed_run_does_not_save_a_profile(tmp_path: Path, profile_home: Path) -> None:
    # Sequential files cannot stack: the run errors, so nothing is saved.
    a, b = _two_sequential_files(tmp_path)
    output = tmp_path / "out.gpx"

    result = runner.invoke(
        app,
        [
            "run",
            str(a),
            str(b),
            "--out",
            str(output),
            "--mode",
            "stack",
            "--save-profile",
            "trainer",
        ],
    )

    assert result.exit_code != 0
    assert profiles.list_profiles(base=profile_home) == []


@pytest.mark.unit
def test_profile_flag_applies_the_saved_format(
    tmp_path: Path, profile_home: Path
) -> None:
    a, b = _two_sequential_files(tmp_path)
    profiles.save_profile("trainer", {"output_format": "tcx"}, base=profile_home)
    output = tmp_path / "out.tcx"

    # No --format on the command line: the profile's tcx must drive the write.
    result = runner.invoke(
        app, ["run", str(a), str(b), "--out", str(output), "--profile", "trainer"]
    )

    assert result.exit_code == 0
    assert output.read_text(encoding="utf-8").startswith("<?xml")
    assert "TrainingCenterDatabase" in output.read_text(encoding="utf-8")


@pytest.mark.unit
def test_explicit_flag_overrides_the_profile(
    tmp_path: Path, profile_home: Path
) -> None:
    a, b = _two_sequential_files(tmp_path)
    profiles.save_profile("trainer", {"output_format": "tcx"}, base=profile_home)
    output = tmp_path / "out.gpx"

    # The profile says tcx, but an explicit --format gpx wins (flag > profile).
    result = runner.invoke(
        app,
        [
            "run",
            str(a),
            str(b),
            "--out",
            str(output),
            "--profile",
            "trainer",
            "--format",
            "gpx",
        ],
    )

    assert result.exit_code == 0
    assert "<gpx" in output.read_text(encoding="utf-8")


@pytest.mark.unit
def test_missing_profile_exits_usage(tmp_path: Path, profile_home: Path) -> None:
    a, b = _two_sequential_files(tmp_path)
    output = tmp_path / "out.gpx"

    result = runner.invoke(
        app, ["run", str(a), str(b), "--out", str(output), "--profile", "nope"]
    )

    assert result.exit_code == EXIT_USAGE
    assert "nope" in result.output


@pytest.mark.unit
def test_profile_list_shows_saved_names(profile_home: Path) -> None:
    profiles.save_profile("trainer", {"mode": "stack"}, base=profile_home)
    profiles.save_profile("commute", {"mode": "append"}, base=profile_home)

    result = runner.invoke(app, ["profile", "list"])

    assert result.exit_code == 0
    assert "trainer" in result.output
    assert "commute" in result.output


@pytest.mark.unit
def test_profile_list_says_none_when_empty(profile_home: Path) -> None:
    result = runner.invoke(app, ["profile", "list"])

    assert result.exit_code == 0
    assert "no profiles yet" in result.output


@pytest.mark.unit
def test_profile_show_prints_saved_keys(profile_home: Path) -> None:
    profiles.save_profile(
        "trainer", {"output_format": "tcx", "mode": "stack"}, base=profile_home
    )

    result = runner.invoke(app, ["profile", "show", "trainer"])

    assert result.exit_code == 0
    assert "output_format=tcx" in result.output
    assert "mode=stack" in result.output


@pytest.mark.unit
def test_profile_show_missing_exits_usage(profile_home: Path) -> None:
    result = runner.invoke(app, ["profile", "show", "nope"])

    assert result.exit_code == EXIT_USAGE


@pytest.mark.unit
def test_profile_rm_deletes_the_profile(profile_home: Path) -> None:
    profiles.save_profile("trainer", {"mode": "stack"}, base=profile_home)

    result = runner.invoke(app, ["profile", "rm", "trainer"])

    assert result.exit_code == 0
    assert profiles.list_profiles(base=profile_home) == []


@pytest.mark.unit
def test_profile_rm_missing_exits_usage(profile_home: Path) -> None:
    result = runner.invoke(app, ["profile", "rm", "nope"])

    assert result.exit_code == EXIT_USAGE


@pytest.mark.unit
def test_save_profile_stores_only_the_keys_the_user_set(
    tmp_path: Path, profile_home: Path
) -> None:
    # Saving with one flag pins one key, not every populated default.
    a, b = _two_sequential_files(tmp_path)
    output = tmp_path / "out.tcx"

    result = runner.invoke(
        app,
        [
            "run",
            str(a),
            str(b),
            "--out",
            str(output),
            "--format",
            "tcx",
            "--save-profile",
            "p",
        ],
    )

    assert result.exit_code == 0
    assert profiles.load_profile("p", base=profile_home) == {"output_format": "tcx"}


@pytest.mark.unit
def test_save_profile_with_an_unsafe_name_exits_usage(
    tmp_path: Path, profile_home: Path
) -> None:
    a, b = _two_sequential_files(tmp_path)
    output = tmp_path / "out.gpx"

    result = runner.invoke(
        app,
        ["run", str(a), str(b), "--out", str(output), "--save-profile", "../escape"],
    )

    assert result.exit_code == EXIT_USAGE


@pytest.mark.unit
def test_profile_key_param_map_covers_every_saveable_key() -> None:
    # Guard against the cli map and the profiles key list drifting apart.
    from gpsmerge.cli import _PROFILE_KEY_TO_PARAM

    assert {key for key, _ in _PROFILE_KEY_TO_PARAM} == set(profiles.SAVEABLE_KEYS)
