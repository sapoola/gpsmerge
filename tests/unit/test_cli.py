"""Tests for the gpsmerge command-line entry point."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from gpsmerge import __version__
from gpsmerge.cli.app import app
from gpsmerge.errors import (
    EXIT_MODE_CONFLICT,
    EXIT_NO_WORKOUT_DATA,
    EXIT_USAGE,
    EXIT_WRITE,
)
from gpsmerge.io.read import read
from gpsmerge.model import Activity, Segment, TrackPoint

runner = CliRunner()


def _write_power_gpx(path: Path, *, start_offset: int, levels: list[int]) -> Path:
    """Write a sequential GPX carrying one 1 Hz power sample per level.

    ``start_offset`` seconds places the file on the timeline so two files can be
    appended back-to-back without overlapping. Positions tick along so GPX (which
    needs a position) is valid.
    """
    from gpsmerge.io.gpx import write_gpx

    base = datetime(2026, 6, 4, 8, 0, 0, tzinfo=UTC)
    points = [
        TrackPoint(
            time=base + timedelta(seconds=start_offset + i),
            lat=52.0 + (start_offset + i) * 1e-5,
            lon=4.0,
            power=level,
        )
        for i, level in enumerate(levels)
    ]
    write_gpx(Activity(sport="cycling", segments=[Segment(points=points)]), path)
    return path


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
def test_run_help_groups_options_into_panels() -> None:
    # The ~30 run flags are grouped under rich_help_panel headings so the help is
    # navigable rather than a flat wall. Each panel title should appear.
    result = runner.invoke(app, ["run", "--help"])

    assert result.exit_code == 0
    for panel in ("Output", "Stacking", "Appending", "Transforms", "Profiles"):
        assert panel in result.output


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


# Matches an ANSI CSI escape sequence (e.g. the SGR colour codes typer/rich emit).
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


@pytest.mark.unit
def test_no_color_env_strips_ansi_from_error_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # NO_COLOR is an established convention (https://no-color.org): when it is set
    # to any value, no ANSI colour codes should appear in the output. The error
    # path (no --out, no --dry-run) is a usage failure that prints in red.
    monkeypatch.setenv("NO_COLOR", "1")
    # FORCE_COLOR would override NO_COLOR; make sure a stray one cannot leak in.
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    a, b = _two_sequential_files(tmp_path)

    # The error goes to stderr; Click's runner captures it separately.
    result = runner.invoke(app, ["run", str(a), str(b)])

    assert result.exit_code == EXIT_USAGE
    assert "--out" in result.stderr  # the message itself is unchanged
    assert not _ANSI.search(result.stderr)  # but it carries no colour codes


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
def test_run_dry_run_without_out_succeeds_and_writes_nothing(tmp_path: Path) -> None:
    # --out is optional for a dry run: the preview is shown, nothing is written.
    a, b = _two_sequential_files(tmp_path)

    result = runner.invoke(app, ["run", str(a), str(b), "--dry-run"])

    assert result.exit_code == 0
    assert "dry run, no file written" in result.output
    assert not (tmp_path / "merged.gpx").exists()
    assert not Path("merged.gpx").exists()


@pytest.mark.unit
def test_run_without_out_and_not_dry_run_is_a_usage_error(tmp_path: Path) -> None:
    # A real write needs somewhere to go: no --out (and not --dry-run) fails fast.
    a, b = _two_sequential_files(tmp_path)

    result = runner.invoke(app, ["run", str(a), str(b)])

    assert result.exit_code == EXIT_USAGE
    assert "--out" in result.output


@pytest.mark.unit
def test_run_dry_run_without_out_json_has_null_output(tmp_path: Path) -> None:
    a, b = _two_sequential_files(tmp_path)

    result = runner.invoke(app, ["run", str(a), str(b), "--dry-run", "--json"])

    assert result.exit_code == 0
    summary = json.loads(result.stdout)
    assert summary["output"] is None
    assert summary["written"] is False


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
def test_run_align_clocks_flag_reaches_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import gpsmerge.cli.app as cli

    gps, sensor = _two_overlapping_files(tmp_path)
    output = tmp_path / "out.gpx"

    seen: list[bool] = []
    real_run = cli.run_pipeline

    def spy(settings: object) -> object:
        seen.append(settings.clock_align)  # type: ignore[attr-defined]
        return real_run(settings)  # type: ignore[arg-type]

    monkeypatch.setattr(cli, "run_pipeline", spy)

    result = runner.invoke(
        app,
        [
            "run",
            str(gps),
            str(sensor),
            "--out",
            str(output),
            "--mode",
            "stack",
            "--align-clocks",
        ],
    )

    assert result.exit_code == 0
    assert seen == [True]


@pytest.mark.unit
def test_run_align_clocks_defaults_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import gpsmerge.cli.app as cli

    gps, sensor = _two_overlapping_files(tmp_path)
    output = tmp_path / "out.gpx"

    seen: list[bool] = []
    real_run = cli.run_pipeline

    def spy(settings: object) -> object:
        seen.append(settings.clock_align)  # type: ignore[attr-defined]
        return real_run(settings)  # type: ignore[arg-type]

    monkeypatch.setattr(cli, "run_pipeline", spy)

    result = runner.invoke(
        app, ["run", str(gps), str(sensor), "--out", str(output), "--mode", "stack"]
    )

    assert result.exit_code == 0
    assert seen == [False]


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


# --- the --auto-lap flag -------------------------------------------------

# A GPX track whose points are spaced ~0.01 deg of latitude apart (~1.11 km),
# so a 1 km auto-lap produces several laps. {trkpts} is filled with the points.
_GPX_TRACK_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<gpx creator="{creator}" version="1.1"
     xmlns="http://www.topografix.com/GPX/1/1">
 <trk>
  <type>cycling</type>
  <trkseg>
{trkpts}
  </trkseg>
 </trk>
</gpx>
"""


def _write_long_gpx(
    path: Path, *, creator: str, lat0: float, start_second: int, count: int
) -> Path:
    """Write a single-segment GPX of ``count`` points ~1.11 km apart, 60 s each."""
    base = datetime(2026, 6, 4, 8, 0, 0)
    rows = []
    for i in range(count):
        lat = lat0 + i * 0.01
        when = (
            base.replace(second=0) + timedelta(seconds=start_second + i * 60)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows.append(
            f'   <trkpt lat="{lat:.5f}" lon="4.00000">\n'
            f"    <ele>5.0</ele>\n"
            f"    <time>{when}</time>\n"
            f"   </trkpt>"
        )
    path.write_text(
        _GPX_TRACK_TEMPLATE.format(creator=creator, trkpts="\n".join(rows)),
        encoding="utf-8",
    )
    return path


@pytest.mark.unit
def test_run_auto_lap_writes_distance_laps_in_a_tcx_round_trip(tmp_path: Path) -> None:
    # Two sequential ~5.5 km tracks; a 1 km auto-lap should split the ~11 km
    # merged activity into several laps that survive a TCX round-trip.
    a = _write_long_gpx(
        tmp_path / "a.gpx", creator="Device A", lat0=52.0, start_second=0, count=6
    )
    b = _write_long_gpx(
        tmp_path / "b.gpx", creator="Device B", lat0=52.1, start_second=600, count=6
    )
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
            "--auto-lap",
            "1km",
        ],
    )

    assert result.exit_code == 0
    written = read(output)
    # Several laps were generated, and they round-tripped through TCX.
    assert len(written.laps) >= 2
    # Completed laps carry the distance trigger (TCX maps it back to "distance").
    assert any(lap.trigger == "distance" for lap in written.laps)


@pytest.mark.unit
def test_run_auto_lap_rejects_an_unparseable_distance(tmp_path: Path) -> None:
    a, b = _two_sequential_files(tmp_path)
    output = tmp_path / "out.gpx"

    result = runner.invoke(
        app, ["run", str(a), str(b), "--out", str(output), "--auto-lap", "1furlong"]
    )

    assert result.exit_code == EXIT_USAGE
    assert not output.exists()


# --- workout detection (beta) -----------------------------------------------

pytest.importorskip("numpy", reason="workout detection needs the numpy extra")


@pytest.mark.unit
def test_run_detect_workout_writes_laps_and_prints_summary(tmp_path: Path) -> None:
    # Two appended power recordings forming a structured workout: ~300 W then
    # ~180 W. TCX is written so laps round-trip.
    a = _write_power_gpx(tmp_path / "a.gpx", start_offset=0, levels=[300] * 120)
    b = _write_power_gpx(tmp_path / "b.gpx", start_offset=130, levels=[180] * 120)
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
            "--detect-workout",
        ],
    )

    assert result.exit_code == 0
    # The BETA heads-up goes to stderr.
    assert "BETA" in result.stderr or "BETA" in result.output
    # The summary is shown in the preview and is in watts.
    assert "W" in result.output and "workout" in result.output
    # Laps were written and round-tripped through TCX.
    written = read(output)
    assert len(written.laps) >= 2


@pytest.mark.unit
def test_run_detect_workout_json_carries_the_summary(tmp_path: Path) -> None:
    a = _write_power_gpx(tmp_path / "a.gpx", start_offset=0, levels=[300] * 120)
    b = _write_power_gpx(tmp_path / "b.gpx", start_offset=130, levels=[180] * 120)
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
            "--detect-workout",
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert "workout" in payload
    assert "W" in payload["workout"]


@pytest.mark.unit
def test_run_detect_workout_with_no_effort_data_is_a_clear_error(
    tmp_path: Path,
) -> None:
    # The two-point GPX fixtures carry no power and no speed, so detection has
    # nothing to work from -> the defined no-workout-data exit code.
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
            "--detect-workout",
        ],
    )

    assert result.exit_code == EXIT_NO_WORKOUT_DATA
    assert "no usable power or speed" in result.output + result.stderr


@pytest.mark.unit
def test_run_auto_lap_and_detect_workout_together_is_a_usage_error(
    tmp_path: Path,
) -> None:
    a = _write_power_gpx(tmp_path / "a.gpx", start_offset=0, levels=[300] * 120)
    b = _write_power_gpx(tmp_path / "b.gpx", start_offset=130, levels=[180] * 120)
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
            "--auto-lap",
            "1km",
            "--detect-workout",
        ],
    )

    assert result.exit_code == EXIT_USAGE
    assert "both set laps" in result.output + result.stderr


@pytest.mark.unit
def test_run_help_lists_the_workout_beta_panel() -> None:
    result = runner.invoke(app, ["run", "--help"])
    assert result.exit_code == 0
    assert "Workout (beta)" in result.output
