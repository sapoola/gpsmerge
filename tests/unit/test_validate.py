"""Tests for the pre-merge validation gate."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from gpsmerge.config import Settings
from gpsmerge.errors import (
    EXIT_MODE_CONFLICT,
    EXIT_PARSE,
    EXIT_USAGE,
    EXIT_WRITE,
)
from gpsmerge.model import Activity, Device, Segment, TrackPoint
from gpsmerge.validate import validate

A_PATH = Path("a.gpx")
B_PATH = Path("b.gpx")
_BASE = datetime(2026, 6, 4, 8, 0, 0, tzinfo=UTC)


def _point(offset: int, time: bool = True) -> TrackPoint:
    """Build a point ``offset`` seconds after the base time, or with no time."""
    return TrackPoint(
        time=_BASE + timedelta(seconds=offset) if time else None,
        lat=52.0,
        lon=4.0,
    )


def _activity(points: list[TrackPoint], *, source: Path) -> Activity:
    return Activity(
        device=Device(name="Garmin Edge 530"),
        segments=[Segment(points=points)],
        source_path=str(source),
    )


def _settings(output: Path, **overrides: object) -> Settings:
    base: dict[str, object] = {"inputs": [A_PATH, B_PATH], "output": output}
    base.update(overrides)
    return Settings.model_validate(base)


def _two_sequential() -> list[Activity]:
    first = _activity([_point(0), _point(10)], source=A_PATH)
    second = _activity([_point(300), _point(310)], source=B_PATH)
    return [first, second]


@pytest.mark.unit
def test_fewer_than_two_activities_is_a_usage_error(tmp_path: Path) -> None:
    one = [_activity([_point(0), _point(10)], source=A_PATH)]
    issues = validate(one, _settings(tmp_path / "out.gpx"))
    assert len(issues) == 1
    assert issues[0].code == EXIT_USAGE


@pytest.mark.unit
def test_activity_without_timestamps_is_a_parse_error(tmp_path: Path) -> None:
    first = _activity([_point(0), _point(10)], source=A_PATH)
    untimed = _activity([_point(0, time=False), _point(0, time=False)], source=B_PATH)
    issues = validate([first, untimed], _settings(tmp_path / "out.gpx"))
    assert any(issue.code == EXIT_PARSE for issue in issues)


@pytest.mark.unit
def test_overlapping_activities_conflict_with_append_mode(tmp_path: Path) -> None:
    first = _activity([_point(0), _point(100)], source=A_PATH)
    # Starts at +50 s, before the first one ends at +100 s.
    second = _activity([_point(50), _point(150)], source=B_PATH)
    issues = validate([first, second], _settings(tmp_path / "out.gpx", mode="append"))
    assert any(issue.code == EXIT_MODE_CONFLICT for issue in issues)


@pytest.mark.unit
def test_non_overlapping_activities_conflict_with_stack_mode(tmp_path: Path) -> None:
    # Stack expects the recordings to overlap; sequential ones are a conflict.
    issues = validate(_two_sequential(), _settings(tmp_path / "out.gpx", mode="stack"))
    assert any(issue.code == EXIT_MODE_CONFLICT for issue in issues)


@pytest.mark.unit
def test_missing_output_directory_is_a_write_error(tmp_path: Path) -> None:
    output = tmp_path / "does_not_exist" / "out.gpx"
    issues = validate(_two_sequential(), _settings(output))
    assert any(issue.code == EXIT_WRITE for issue in issues)


@pytest.mark.unit
def test_existing_output_without_yes_is_a_write_error(tmp_path: Path) -> None:
    output = tmp_path / "out.gpx"
    output.write_text("already here", encoding="utf-8")
    issues = validate(_two_sequential(), _settings(output, assume_yes=False))
    assert any(issue.code == EXIT_WRITE for issue in issues)


@pytest.mark.unit
def test_existing_output_with_yes_is_allowed(tmp_path: Path) -> None:
    output = tmp_path / "out.gpx"
    output.write_text("already here", encoding="utf-8")
    issues = validate(_two_sequential(), _settings(output, assume_yes=True))
    assert not any(issue.code == EXIT_WRITE for issue in issues)


@pytest.mark.unit
def test_two_sequential_with_writable_output_has_no_issues(tmp_path: Path) -> None:
    output = tmp_path / "out.gpx"
    issues = validate(_two_sequential(), _settings(output))
    assert issues == []


@pytest.mark.unit
def test_dry_run_skips_output_path_checks(tmp_path: Path) -> None:
    # A dry run writes nothing, so an existing output must not block the preview.
    output = tmp_path / "out.gpx"
    output.write_text("already here", encoding="utf-8")
    issues = validate(_two_sequential(), _settings(output, dry_run=True))
    assert issues == []
