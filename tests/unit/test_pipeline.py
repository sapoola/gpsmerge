"""Tests for the end-to-end run pipeline (read, validate, merge, write)."""

from __future__ import annotations

from pathlib import Path

import pytest

from gpsmerge.config import Settings
from gpsmerge.errors import EXIT_MODE_CONFLICT, EXIT_PARSE
from gpsmerge.pipeline import run

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


def _settings(inputs: list[Path], output: Path, **overrides: object) -> Settings:
    base: dict[str, object] = {"inputs": inputs, "output": output}
    base.update(overrides)
    return Settings.model_validate(base)


@pytest.mark.unit
def test_two_sequential_files_merge_and_write(tmp_path: Path) -> None:
    inputs = _two_sequential_files(tmp_path)
    output = tmp_path / "out.gpx"

    result = run(_settings(inputs, output))

    assert result.issues == []
    assert result.written is True
    assert result.output == output
    assert output.exists()

    preview = result.preview
    assert preview is not None
    assert preview.point_count == 4
    assert preview.distance_m > 0.0
    assert preview.device_name == "Garmin Edge 530"  # first input by default
    assert "position" in preview.channels


@pytest.mark.unit
def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    inputs = _two_sequential_files(tmp_path)
    output = tmp_path / "out.gpx"

    result = run(_settings(inputs, output, dry_run=True))

    assert result.issues == []
    assert result.written is False
    assert not output.exists()


@pytest.mark.unit
def test_unreadable_input_is_a_parse_error(tmp_path: Path) -> None:
    good = _two_sequential_files(tmp_path)[0]
    bad = tmp_path / "broken.txt"  # wrong extension: not a GPX we can read
    bad.write_text("not a gpx file", encoding="utf-8")
    output = tmp_path / "out.gpx"

    result = run(_settings([good, bad], output))

    assert any(issue.code == EXIT_PARSE for issue in result.issues)
    assert not output.exists()


@pytest.mark.unit
def test_overlapping_inputs_are_a_mode_conflict(tmp_path: Path) -> None:
    first = _write_gpx(
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
    second = _write_gpx(
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

    result = run(_settings([first, second], output))

    assert any(issue.code == EXIT_MODE_CONFLICT for issue in result.issues)
    assert not output.exists()


@pytest.mark.unit
def test_large_seam_jump_produces_a_warning(tmp_path: Path) -> None:
    first = _write_gpx(
        tmp_path / "a.gpx",
        creator="Device A",
        lat1=52.000,
        lon1=4.000,
        lat2=52.001,
        lon2=4.001,
        time1="2026-06-04T08:00:00Z",
        time2="2026-06-04T08:00:10Z",
    )
    # The second recording is ~100 km away: a large position jump at the seam.
    second = _write_gpx(
        tmp_path / "b.gpx",
        creator="Device B",
        lat1=53.000,
        lon1=5.000,
        lat2=53.001,
        lon2=5.001,
        time1="2026-06-04T08:05:00Z",
        time2="2026-06-04T08:05:10Z",
    )
    output = tmp_path / "out.gpx"

    result = run(_settings([first, second], output))

    assert result.preview is not None
    assert result.preview.warnings
