"""Tests for the gpsmerge command-line entry point."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from gpsmerge import __version__
from gpsmerge.cli import app
from gpsmerge.errors import EXIT_MODE_CONFLICT, EXIT_USAGE, EXIT_WRITE
from gpsmerge.read import read

runner = CliRunner()


@pytest.mark.unit
def test_version_flag_prints_version_and_exits() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


@pytest.mark.unit
def test_bare_invocation_succeeds() -> None:
    result = runner.invoke(app, [])
    assert result.exit_code == 0


# --- the 'run' command ---------------------------------------------------

# A tiny GPX with one segment of two points; placeholders are filled per file.
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


def _write_gpx(
    path: Path,
    *,
    creator: str,
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
    time1: str,
    time2: str,
) -> Path:
    """Write a two-point GPX file and return its path."""
    path.write_text(
        _GPX_TEMPLATE.format(
            creator=creator,
            lat1=lat1,
            lon1=lon1,
            lat2=lat2,
            lon2=lon2,
            time1=time1,
            time2=time2,
        ),
        encoding="utf-8",
    )
    return path


def _two_sequential_files(tmp_path: Path) -> list[Path]:
    """Write two non-overlapping GPX recordings a few minutes apart."""
    first = _write_gpx(
        tmp_path / "a.gpx",
        creator="Garmin Edge 530",
        lat1=52.000,
        lon1=4.000,
        lat2=52.001,
        lon2=4.001,
        time1="2026-06-04T08:00:00Z",
        time2="2026-06-04T08:00:10Z",
    )
    second = _write_gpx(
        tmp_path / "b.gpx",
        creator="Stages Dash M50",
        lat1=52.002,
        lon1=4.002,
        lat2=52.003,
        lon2=4.003,
        time1="2026-06-04T08:05:00Z",
        time2="2026-06-04T08:05:10Z",
    )
    return [first, second]


@pytest.mark.unit
def test_run_merges_two_sequential_files(tmp_path: Path) -> None:
    a, b = _two_sequential_files(tmp_path)
    output = tmp_path / "out.gpx"

    result = runner.invoke(app, ["run", str(a), str(b), "--out", str(output)])

    assert result.exit_code == 0
    assert output.exists()


@pytest.mark.unit
def test_run_rejects_unavailable_mode(tmp_path: Path) -> None:
    a, b = _two_sequential_files(tmp_path)
    output = tmp_path / "out.gpx"

    result = runner.invoke(
        app, ["run", str(a), str(b), "--out", str(output), "--mode", "stack"]
    )

    assert result.exit_code == EXIT_USAGE


@pytest.mark.unit
def test_run_rejects_a_single_input(tmp_path: Path) -> None:
    a, _ = _two_sequential_files(tmp_path)
    output = tmp_path / "out.gpx"

    result = runner.invoke(app, ["run", str(a), "--out", str(output)])

    assert result.exit_code == EXIT_USAGE


@pytest.mark.unit
def test_run_rejects_overlapping_inputs(tmp_path: Path) -> None:
    a = _write_gpx(
        tmp_path / "a.gpx",
        creator="Device A",
        lat1=52.000,
        lon1=4.000,
        lat2=52.001,
        lon2=4.001,
        time1="2026-06-04T08:00:00Z",
        time2="2026-06-04T08:10:00Z",
    )
    # Starts inside the first recording's window: the two overlap in time.
    b = _write_gpx(
        tmp_path / "b.gpx",
        creator="Device B",
        lat1=52.002,
        lon1=4.002,
        lat2=52.003,
        lon2=4.003,
        time1="2026-06-04T08:05:00Z",
        time2="2026-06-04T08:15:00Z",
    )
    output = tmp_path / "out.gpx"

    result = runner.invoke(app, ["run", str(a), str(b), "--out", str(output)])

    assert result.exit_code == EXIT_MODE_CONFLICT


@pytest.mark.unit
def test_run_refuses_to_overwrite_without_yes(tmp_path: Path) -> None:
    a, b = _two_sequential_files(tmp_path)
    output = tmp_path / "out.gpx"
    output.write_text("already here", encoding="utf-8")

    result = runner.invoke(app, ["run", str(a), str(b), "--out", str(output)])

    assert result.exit_code == EXIT_WRITE


@pytest.mark.unit
def test_run_overwrites_with_yes(tmp_path: Path) -> None:
    a, b = _two_sequential_files(tmp_path)
    output = tmp_path / "out.gpx"
    output.write_text("already here", encoding="utf-8")

    result = runner.invoke(app, ["run", str(a), str(b), "--out", str(output), "-y"])

    assert result.exit_code == 0


@pytest.mark.unit
def test_run_dry_run_writes_nothing(tmp_path: Path) -> None:
    a, b = _two_sequential_files(tmp_path)
    output = tmp_path / "out.gpx"

    result = runner.invoke(
        app, ["run", str(a), str(b), "--out", str(output), "--dry-run"]
    )

    assert result.exit_code == 0
    assert not output.exists()


@pytest.mark.unit
def test_run_json_output_is_machine_readable(tmp_path: Path) -> None:
    a, b = _two_sequential_files(tmp_path)
    output = tmp_path / "out.gpx"

    result = runner.invoke(app, ["run", str(a), str(b), "--out", str(output), "--json"])

    assert result.exit_code == 0
    summary = json.loads(result.stdout)
    for key in ("points", "distance_m", "device", "channels", "written"):
        assert key in summary


@pytest.mark.unit
def test_run_device_from_attributes_the_chosen_input(tmp_path: Path) -> None:
    a, b = _two_sequential_files(tmp_path)
    output = tmp_path / "out.gpx"

    result = runner.invoke(
        app, ["run", str(a), str(b), "--out", str(output), "--device-from", str(b)]
    )

    assert result.exit_code == 0
    written = read(output)
    assert written.device.name == "Stages Dash M50"  # the second file's device
