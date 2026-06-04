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
    # The CliRunner's stdin is not a TTY, so bare invocation prints the hint
    # and exits 0 without launching (or hanging on) the wizard.
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "guided wizard" in result.output


@pytest.mark.unit
def test_examples_command_prints_real_commands() -> None:
    result = runner.invoke(app, ["examples"])
    assert result.exit_code == 0
    assert "gpsmerge run" in result.output
    assert "--mode stack" in result.output
    assert "--power-from" in result.output


@pytest.mark.unit
def test_formats_command_lists_formats_and_channels() -> None:
    result = runner.invoke(app, ["formats"])
    assert result.exit_code == 0
    assert "gpx" in result.output
    assert "tcx" in result.output
    assert "no temperature" in result.output
    assert "power" in result.output


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
def test_run_rejects_an_invalid_mode(tmp_path: Path) -> None:
    a, b = _two_sequential_files(tmp_path)
    output = tmp_path / "out.gpx"

    result = runner.invoke(
        app, ["run", str(a), str(b), "--out", str(output), "--mode", "sideways"]
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
def test_run_timezone_prints_a_local_line_with_the_offset(tmp_path: Path) -> None:
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
            "--timezone",
            "Europe/Amsterdam",
        ],
    )

    assert result.exit_code == 0
    assert "time:" in result.output
    assert "local:" in result.output
    # June is summer time in Amsterdam: +02:00.
    assert "Europe/Amsterdam, UTC+02:00" in result.output


@pytest.mark.unit
def test_run_without_timezone_prints_time_but_no_local_line(tmp_path: Path) -> None:
    a, b = _two_sequential_files(tmp_path)
    output = tmp_path / "out.gpx"

    result = runner.invoke(
        app, ["run", str(a), str(b), "--out", str(output), "--dry-run"]
    )

    assert result.exit_code == 0
    assert "time:" in result.output
    assert "UTC" in result.output
    assert "local:" not in result.output


@pytest.mark.unit
def test_run_json_includes_utc_and_local_times_with_a_timezone(tmp_path: Path) -> None:
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
            "--json",
            "--timezone",
            "Europe/Amsterdam",
        ],
    )

    assert result.exit_code == 0
    summary = json.loads(result.stdout)
    assert summary["start_utc"] == "2026-06-04T08:00:00+00:00"
    assert summary["timezone"] == "Europe/Amsterdam"
    assert summary["local_start"] == "2026-06-04T10:00:00+02:00"
    assert summary["local_end"].endswith("+02:00")


@pytest.mark.unit
def test_run_json_omits_local_fields_without_a_timezone(tmp_path: Path) -> None:
    a, b = _two_sequential_files(tmp_path)
    output = tmp_path / "out.gpx"

    result = runner.invoke(app, ["run", str(a), str(b), "--out", str(output), "--json"])

    assert result.exit_code == 0
    summary = json.loads(result.stdout)
    assert "start_utc" in summary
    assert "local_start" not in summary
    assert "timezone" not in summary


@pytest.mark.unit
def test_run_rejects_an_unparseable_timezone(tmp_path: Path) -> None:
    a, b = _two_sequential_files(tmp_path)
    output = tmp_path / "out.gpx"

    result = runner.invoke(
        app,
        ["run", str(a), str(b), "--out", str(output), "--timezone", "Mars/Olympus"],
    )

    assert result.exit_code == EXIT_USAGE
    assert not output.exists()


@pytest.mark.unit
def test_run_trim_flags_reduce_the_point_count(tmp_path: Path) -> None:
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
            "--json",
            "--trim-start",
            "2026-06-04T08:00:00Z",
            "--trim-end",
            "2026-06-04T08:00:10Z",
        ],
    )

    assert result.exit_code == 0
    summary = json.loads(result.stdout)
    assert summary["points"] == 2  # only the first recording's two points survive


@pytest.mark.unit
def test_run_shift_moves_the_preview_start_time(tmp_path: Path) -> None:
    a, b = _two_sequential_files(tmp_path)
    output = tmp_path / "out.gpx"

    result = runner.invoke(
        app,
        ["run", str(a), str(b), "--out", str(output), "--json", "--shift", "+1h"],
    )

    assert result.exit_code == 0
    summary = json.loads(result.stdout)
    assert summary["start_utc"] == "2026-06-04T09:00:00+00:00"


@pytest.mark.unit
def test_run_rejects_an_unparseable_shift(tmp_path: Path) -> None:
    a, b = _two_sequential_files(tmp_path)
    output = tmp_path / "out.gpx"

    result = runner.invoke(
        app, ["run", str(a), str(b), "--out", str(output), "--shift", "soon"]
    )

    assert result.exit_code == EXIT_USAGE
    assert not output.exists()


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


# --- stacking and auto mode-detection ------------------------------------

# A GPX whose points carry heart rate via the Garmin TrackPointExtension, so a
# stacked merge can borrow the channel from one file onto another's positions.
_GPX_HR_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<gpx creator="{creator}" version="1.1"
     xmlns="http://www.topografix.com/GPX/1/1"
     xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1">
 <trk>
  <type>cycling</type>
  <trkseg>
   <trkpt lat="{lat1}" lon="{lon1}">
    <ele>5.0</ele>
    <time>{time1}</time>
    <extensions><gpxtpx:TrackPointExtension>
     <gpxtpx:hr>{hr1}</gpxtpx:hr>
    </gpxtpx:TrackPointExtension></extensions>
   </trkpt>
   <trkpt lat="{lat2}" lon="{lon2}">
    <ele>6.0</ele>
    <time>{time2}</time>
    <extensions><gpxtpx:TrackPointExtension>
     <gpxtpx:hr>{hr2}</gpxtpx:hr>
    </gpxtpx:TrackPointExtension></extensions>
   </trkpt>
  </trkseg>
 </trk>
</gpx>
"""


def _write_gpx_with_hr(
    path: Path, *, creator: str, time1: str, time2: str, hr1: int, hr2: int
) -> Path:
    """Write a two-point GPX carrying heart rate, at a fixed position."""
    path.write_text(
        _GPX_HR_TEMPLATE.format(
            creator=creator,
            lat1=52.000,
            lon1=4.000,
            lat2=52.001,
            lon2=4.001,
            time1=time1,
            time2=time2,
            hr1=hr1,
            hr2=hr2,
        ),
        encoding="utf-8",
    )
    return path


def _two_overlapping_files(tmp_path: Path) -> list[Path]:
    """Write two recordings of the same window: a GPS-only track and an HR track."""
    gps = _write_gpx(
        tmp_path / "gps.gpx",
        creator="Garmin Watch",
        lat1=52.000,
        lon1=4.000,
        lat2=52.001,
        lon2=4.001,
        time1="2026-06-04T08:00:00Z",
        time2="2026-06-04T08:00:10Z",
    )
    sensor = _write_gpx_with_hr(
        tmp_path / "hr.gpx",
        creator="HR Strap",
        time1="2026-06-04T08:00:00Z",
        time2="2026-06-04T08:00:10Z",
        hr1=120,
        hr2=130,
    )
    return [gps, sensor]


@pytest.mark.unit
def test_run_stack_overlapping_files_writes_output_with_the_donor_channel(
    tmp_path: Path,
) -> None:
    gps, sensor = _two_overlapping_files(tmp_path)
    output = tmp_path / "out.gpx"

    result = runner.invoke(
        app,
        ["run", str(gps), str(sensor), "--out", str(output), "--mode", "stack"],
    )

    assert result.exit_code == 0
    assert output.exists()
    # The GPS-only master borrowed heart rate from the strap.
    written = read(output)
    assert any(p.hr is not None for p in written.points())


@pytest.mark.unit
def test_run_stack_on_non_overlapping_files_is_a_mode_conflict(tmp_path: Path) -> None:
    a, b = _two_sequential_files(tmp_path)
    output = tmp_path / "out.gpx"

    result = runner.invoke(
        app, ["run", str(a), str(b), "--out", str(output), "--mode", "stack"]
    )

    assert result.exit_code == EXIT_MODE_CONFLICT


@pytest.mark.unit
def test_run_auto_stacks_two_fully_overlapping_files(tmp_path: Path) -> None:
    gps, sensor = _two_overlapping_files(tmp_path)
    output = tmp_path / "out.gpx"

    # No --mode: auto sees the identical windows and stacks them.
    result = runner.invoke(app, ["run", str(gps), str(sensor), "--out", str(output)])

    assert result.exit_code == 0
    assert output.exists()


@pytest.mark.unit
def test_run_auto_on_partial_overlap_is_a_mode_conflict(tmp_path: Path) -> None:
    # Each recording spans 100 s and they share 50 s: a ~0.5 overlap ratio that
    # auto cannot resolve into stack or append.
    a = _write_gpx(
        tmp_path / "a.gpx",
        creator="Device A",
        lat1=52.000,
        lon1=4.000,
        lat2=52.001,
        lon2=4.001,
        time1="2026-06-04T08:00:00Z",
        time2="2026-06-04T08:01:40Z",
    )
    b = _write_gpx(
        tmp_path / "b.gpx",
        creator="Device B",
        lat1=52.002,
        lon1=4.002,
        lat2=52.003,
        lon2=4.003,
        time1="2026-06-04T08:00:50Z",
        time2="2026-06-04T08:02:30Z",
    )
    output = tmp_path / "out.gpx"

    result = runner.invoke(app, ["run", str(a), str(b), "--out", str(output)])

    assert result.exit_code == EXIT_MODE_CONFLICT
