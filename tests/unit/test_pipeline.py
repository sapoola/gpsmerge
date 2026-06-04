"""Tests for the end-to-end run pipeline (read, validate, merge, write)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from gpsmerge.config import Settings
from gpsmerge.errors import EXIT_MODE_CONFLICT, EXIT_PARSE, EXIT_USAGE
from gpsmerge.model import Activity, TrackPoint
from gpsmerge.pipeline import _total_distance, run
from gpsmerge.write import write

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


@pytest.mark.unit
def test_preview_carries_utc_start_and_end_from_the_points(tmp_path: Path) -> None:
    inputs = _two_sequential_files(tmp_path)
    output = tmp_path / "out.gpx"

    result = run(_settings(inputs, output, dry_run=True))

    preview = result.preview
    assert preview is not None
    assert preview.start_time == datetime(2026, 6, 4, 8, 0, 0, tzinfo=UTC)
    assert preview.end_time == datetime(2026, 6, 4, 8, 5, 10, tzinfo=UTC)


@pytest.mark.unit
def test_timezone_shifts_the_preview_local_times_by_the_zone_offset(
    tmp_path: Path,
) -> None:
    # Europe/Amsterdam is +02:00 in June: 08:00:00 UTC shows as 10:00:00 local,
    # while the underlying UTC start is unchanged.
    inputs = _two_sequential_files(tmp_path)
    output = tmp_path / "out.gpx"

    result = run(_settings(inputs, output, dry_run=True, timezone="Europe/Amsterdam"))

    preview = result.preview
    assert preview is not None
    assert preview.timezone_label == "Europe/Amsterdam"
    assert preview.local_start is not None
    assert preview.local_start.utcoffset() == timedelta(hours=2)
    # Same instant, just rendered in the local zone.
    assert preview.local_start == preview.start_time
    assert preview.local_start.hour == 10


@pytest.mark.unit
def test_no_timezone_leaves_local_times_unset(tmp_path: Path) -> None:
    inputs = _two_sequential_files(tmp_path)
    output = tmp_path / "out.gpx"

    result = run(_settings(inputs, output, dry_run=True))

    preview = result.preview
    assert preview is not None
    assert preview.local_start is None
    assert preview.local_end is None
    assert preview.timezone_label is None


@pytest.mark.unit
def test_unparseable_timezone_is_a_usage_error(tmp_path: Path) -> None:
    inputs = _two_sequential_files(tmp_path)
    output = tmp_path / "out.gpx"

    result = run(_settings(inputs, output, dry_run=True, timezone="Mars/Olympus"))

    assert any(issue.code == EXIT_USAGE for issue in result.issues)


# --- post-merge transforms ----------------------------------------------


@pytest.mark.unit
def test_trim_reduces_the_preview_point_count(tmp_path: Path) -> None:
    inputs = _two_sequential_files(tmp_path)
    output = tmp_path / "out.gpx"

    # The four points span 08:00:00 to 08:05:10; trimming to the first recording
    # keeps only its two points.
    result = run(
        _settings(
            inputs,
            output,
            dry_run=True,
            trim_start="2026-06-04T08:00:00Z",
            trim_end="2026-06-04T08:00:10Z",
        )
    )

    assert result.preview is not None
    assert result.preview.point_count == 2


@pytest.mark.unit
def test_shift_moves_the_preview_start_time(tmp_path: Path) -> None:
    inputs = _two_sequential_files(tmp_path)
    output = tmp_path / "out.gpx"

    result = run(_settings(inputs, output, dry_run=True, shift="+1h"))

    assert result.preview is not None
    assert result.preview.start_time == datetime(2026, 6, 4, 9, 0, 0, tzinfo=UTC)


@pytest.mark.unit
def test_insert_stopped_time_lowers_moving_time_below_elapsed(tmp_path: Path) -> None:
    # The two recordings are 5 minutes apart; once that gap is a pause, moving
    # time (within segments) is well below the elapsed wall-clock span.
    inputs = _two_sequential_files(tmp_path)
    output = tmp_path / "out.gpx"

    result = run(
        _settings(
            inputs,
            output,
            dry_run=True,
            insert_stopped_time=True,
            stopped_time_threshold_s=30.0,
        )
    )

    assert result.preview is not None
    assert result.preview.moving_seconds < result.preview.elapsed_seconds


@pytest.mark.unit
def test_unparseable_shift_is_a_usage_error(tmp_path: Path) -> None:
    inputs = _two_sequential_files(tmp_path)
    output = tmp_path / "out.gpx"

    result = run(_settings(inputs, output, dry_run=True, shift="soon"))

    assert any(issue.code == EXIT_USAGE for issue in result.issues)


# --- preview distance ----------------------------------------------------


@pytest.mark.unit
def test_total_distance_uses_max_recorded_when_last_point_omits_it() -> None:
    # A device often stops emitting cumulative distance while stationary, so the
    # last point's distance is None even though an earlier point holds the true
    # total. The preview must report that total (5.0), not 0.
    points = [
        TrackPoint(time=None, distance=0.0),
        TrackPoint(time=None, distance=5.0),
        TrackPoint(time=None, distance=None),
    ]

    assert _total_distance(points) == 5.0


@pytest.mark.unit
def test_total_distance_recomputes_only_when_no_point_records_it() -> None:
    # No recorded distance anywhere: fall back to a position recompute, which is
    # zero for two coincident points.
    points = [
        TrackPoint(time=None, lat=52.0, lon=4.0),
        TrackPoint(time=None, lat=52.0, lon=4.0),
    ]

    assert _total_distance(points) == 0.0


# --- atomic write --------------------------------------------------------


@pytest.mark.unit
def test_failed_write_leaves_an_existing_destination_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Pre-existing output a person would not want truncated by a crashed write.
    output = tmp_path / "out.gpx"
    output.write_text("ORIGINAL CONTENTS", encoding="utf-8")

    def _boom(activity: Activity, path: Path) -> None:
        # Simulate a writer that fails after the temp file has been created.
        path.write_text("HALF WRITTEN", encoding="utf-8")
        raise OSError("disk full")

    monkeypatch.setitem(write.__globals__["_WRITERS"], "gpx", _boom)

    with pytest.raises(OSError):
        write(Activity(), output, "gpx")

    # The original survives untouched, and no stray temp file is left behind.
    assert output.read_text(encoding="utf-8") == "ORIGINAL CONTENTS"
    assert list(tmp_path.iterdir()) == [output]


@pytest.mark.unit
def test_atomic_write_replaces_the_destination_on_success(tmp_path: Path) -> None:
    # The successful path still overwrites cleanly and leaves no temp file.
    inputs = _two_sequential_files(tmp_path)
    output = tmp_path / "out.gpx"
    output.write_text("STALE", encoding="utf-8")

    result = run(_settings(inputs, output, assume_yes=True))

    assert result.written is True
    assert output.read_text(encoding="utf-8") != "STALE"
    assert not any(p.name.endswith(".tmp") for p in tmp_path.iterdir())
