"""Tests for the interactive guided wizard.

The wizard's only side effects are the questionary prompts it shows and the file
it writes. Each test feeds a full sequence of canned answers by monkeypatching
the questionary functions the wizard calls, then asserts the output file was
produced and reads it back to confirm it is correct. This exercises the wizard's
real logic -- file validation, mode detection and override, channel selection,
device choice, and the final pipeline run -- without a terminal.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from gpsmerge import wizard
from gpsmerge.io.read import read

# A two-point GPX with one segment; placeholders are filled per file.
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

# A two-point GPX carrying heart rate, for stacking a sensor onto a GPS track.
_GPX_HR_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<gpx creator="{creator}" version="1.1"
     xmlns="http://www.topografix.com/GPX/1/1"
     xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1">
 <trk>
  <type>cycling</type>
  <trkseg>
   <trkpt lat="52.000" lon="4.000">
    <ele>5.0</ele>
    <time>{time1}</time>
    <extensions><gpxtpx:TrackPointExtension>
     <gpxtpx:hr>{hr1}</gpxtpx:hr>
    </gpxtpx:TrackPointExtension></extensions>
   </trkpt>
   <trkpt lat="52.001" lon="4.001">
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


def _write_gpx(
    path: Path,
    *,
    creator: str,
    lat1: float = 52.000,
    lon1: float = 4.000,
    lat2: float = 52.001,
    lon2: float = 4.001,
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


def _write_gpx_with_hr(
    path: Path, *, creator: str, time1: str, time2: str, hr1: int, hr2: int
) -> Path:
    """Write a two-point GPX carrying heart rate, at a fixed position."""
    path.write_text(
        _GPX_HR_TEMPLATE.format(
            creator=creator, time1=time1, time2=time2, hr1=hr1, hr2=hr2
        ),
        encoding="utf-8",
    )
    return path


class _Answer:
    """A stand-in for a questionary ``Question`` whose ``ask()`` is canned."""

    def __init__(self, value: Any) -> None:
        self._value = value

    def ask(self) -> Any:
        return self._value


class _ScriptedPrompts:
    """Replaces questionary's prompt functions with answers from a script.

    Each questionary call (``select``, ``text``, ``path``, ``confirm``) pops the
    next answer from its own queue, so a test states exactly what the user types,
    in order, per prompt kind.
    """

    def __init__(self, **queues: list[Any]) -> None:
        self._queues = {name: list(values) for name, values in queues.items()}

    def _make(self, name: str) -> Any:
        def prompt(*_args: Any, **_kwargs: Any) -> _Answer:
            queue = self._queues.get(name)
            if not queue:
                raise AssertionError(f"unexpected questionary.{name} call")
            return _Answer(queue.pop(0))

        return prompt

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for name in ("select", "text", "path", "confirm"):
            monkeypatch.setattr(wizard.questionary, name, self._make(name))


def _two_sequential_files(tmp_path: Path) -> tuple[Path, Path]:
    """Write two non-overlapping recordings five minutes apart."""
    first = _write_gpx(
        tmp_path / "a.gpx",
        creator="Garmin Edge 530",
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
    return first, second


def _two_overlapping_files(tmp_path: Path) -> tuple[Path, Path]:
    """Write a GPS-only track and an HR track over the same window."""
    gps = _write_gpx(
        tmp_path / "gps.gpx",
        creator="Garmin Watch",
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
    return gps, sensor


@pytest.mark.unit
def test_append_two_sequential_files_writes_a_merged_gpx(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a, b = _two_sequential_files(tmp_path)
    output = tmp_path / "merged.gpx"

    _ScriptedPrompts(
        select=[
            "append",  # what do you want to do
            # detection agrees with the explicit choice, so no re-confirm prompt
            "pause",  # gap policy
            "Garmin Edge 530",  # device to record under
            "gpx",  # output format
        ],
        path=[str(a), str(b), "", str(output)],  # two files, blank to finish, output
        # timezone (skip), sport (keep source), auto-lap (skip), profile (skip)
        text=["", "", "", ""],
        confirm=[
            False,  # ignore big gaps?
            True,  # apply standard clean-up
            True,  # write it?
        ],
    ).install(monkeypatch)

    code = wizard.run()

    assert code == 0
    assert output.exists()
    written = read(output)
    assert written.device.name == "Garmin Edge 530"
    # Both recordings' points made it through, in time order.
    times = [p.time for p in written.points() if p.time is not None]
    assert len(times) == 4
    assert times == sorted(times)


@pytest.mark.unit
def test_timezone_answer_reaches_the_pipeline_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A non-blank timezone answer must end up in the Settings the wizard runs.
    a, b = _two_sequential_files(tmp_path)
    output = tmp_path / "merged.gpx"

    seen: list[str | None] = []
    real_run = wizard.run_pipeline

    def spy(settings: Any) -> Any:
        seen.append(settings.timezone)
        return real_run(settings)

    monkeypatch.setattr(wizard, "run_pipeline", spy)

    _ScriptedPrompts(
        select=["append", "pause", "Garmin Edge 530", "gpx"],
        path=[str(a), str(b), "", str(output)],
        # timezone, sport (skip), auto-lap (skip), profile (skip)
        text=["Europe/Amsterdam", "", "", ""],
        confirm=[False, True, True],  # big gaps, clean-up, write it
    ).install(monkeypatch)

    code = wizard.run()

    assert code == 0
    # Both the preview run and the final write run carry the timezone.
    assert seen
    assert all(spec == "Europe/Amsterdam" for spec in seen)


@pytest.mark.unit
def test_stack_two_overlapping_files_borrows_the_sensor_channel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gps, sensor = _two_overlapping_files(tmp_path)
    output = tmp_path / "stacked.gpx"

    _ScriptedPrompts(
        select=[
            "stack",  # what do you want to do
            # detection agrees with the explicit choice, so no re-confirm prompt
            str(gps),  # which file provides position
            "Garmin Watch",  # device to record under
            "gpx",  # output format
        ],
        path=[str(gps), str(sensor), "", str(output)],
        # timezone (skip), sport (keep source), auto-lap (skip), profile (skip)
        text=["", "", "", ""],
        confirm=[
            False,  # clocks out of sync? (decline alignment)
            True,  # apply standard clean-up
            True,  # write it?
        ],
    ).install(monkeypatch)

    code = wizard.run()

    assert code == 0
    assert output.exists()
    written = read(output)
    # The GPS-only master borrowed heart rate from the strap.
    assert any(p.hr is not None for p in written.points())


@pytest.mark.unit
def test_stack_clock_align_answer_reaches_the_pipeline_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Saying yes to "were the clocks out of sync?" must set clock_align on the
    # Settings the wizard runs, on both the preview and the final write.
    gps, sensor = _two_overlapping_files(tmp_path)
    output = tmp_path / "stacked.gpx"

    seen: list[bool] = []
    real_run = wizard.run_pipeline

    def spy(settings: Any) -> Any:
        seen.append(settings.clock_align)
        return real_run(settings)

    monkeypatch.setattr(wizard, "run_pipeline", spy)

    _ScriptedPrompts(
        select=["stack", str(gps), "Garmin Watch", "gpx"],
        path=[str(gps), str(sensor), "", str(output)],
        # timezone (skip), sport (keep source), auto-lap (skip), profile (skip)
        text=["", "", "", ""],
        confirm=[
            True,  # clocks out of sync? -> align them
            True,  # clean-up
            True,  # write it?
        ],
    ).install(monkeypatch)

    code = wizard.run()

    assert code == 0
    assert seen
    assert all(flag is True for flag in seen)


@pytest.mark.unit
def test_contradicting_mode_choice_is_recovered_not_crashed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The files are sequential (an append), but the user first says "stack".
    # The wizard must offer the recommended switch and proceed, not raise.
    a, b = _two_sequential_files(tmp_path)
    output = tmp_path / "recovered.gpx"

    _ScriptedPrompts(
        select=[
            "stack",  # user's first (wrong) intent
            "append",  # the offered recommended switch, taken
            "pause",  # gap policy
            "Garmin Edge 530",  # device
            "gpx",  # format
        ],
        path=[str(a), str(b), "", str(output)],
        # timezone (skip), sport (keep source), auto-lap (skip), profile (skip)
        text=["", "", "", ""],
        confirm=[
            False,  # ignore big gaps?
            True,  # clean-up
            True,  # write it?
        ],
    ).install(monkeypatch)

    code = wizard.run()

    assert code == 0
    assert output.exists()


@pytest.mark.unit
def test_existing_output_path_overwrite_is_honoured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a, b = _two_sequential_files(tmp_path)
    output = tmp_path / "merged.gpx"
    output.write_text("already here", encoding="utf-8")

    _ScriptedPrompts(
        select=[
            "append",
            "pause",
            "Garmin Edge 530",
            "gpx",
            "overwrite",  # the existing-file choice
        ],
        path=[str(a), str(b), "", str(output)],
        # timezone (skip), sport (keep source), auto-lap (skip), profile (skip)
        text=["", "", "", ""],
        confirm=[
            False,  # ignore big gaps?
            True,  # clean-up
            True,  # write it?
        ],
    ).install(monkeypatch)

    code = wizard.run()

    assert code == 0
    # The placeholder was overwritten with a real merge.
    written = read(output)
    assert len(list(written.points())) == 4


@pytest.mark.unit
def test_fewer_than_two_files_then_blank_keeps_prompting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A blank at the first prompt does not finish the list (need >= 2); the
    # wizard keeps asking until two valid files are given.
    a, b = _two_sequential_files(tmp_path)
    output = tmp_path / "merged.gpx"

    _ScriptedPrompts(
        select=["append", "pause", "Garmin Edge 530", "gpx"],
        path=["", str(a), str(b), "", str(output)],  # leading blank ignored
        # timezone (skip), sport (skip), auto-lap (skip), profile (skip)
        text=["", "", "", ""],
        confirm=[False, True, True],
    ).install(monkeypatch)

    code = wizard.run()

    assert code == 0
    assert output.exists()


@pytest.mark.unit
def test_nonexistent_then_valid_path_is_reprompted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a, b = _two_sequential_files(tmp_path)
    output = tmp_path / "merged.gpx"
    missing = tmp_path / "nope.gpx"

    _ScriptedPrompts(
        select=["append", "pause", "Garmin Edge 530", "gpx"],
        # First path does not exist: rejected and re-prompted.
        path=[str(missing), str(a), str(b), "", str(output)],
        # timezone (skip), sport (skip), auto-lap (skip), profile (skip)
        text=["", "", "", ""],
        confirm=[False, True, True],
    ).install(monkeypatch)

    code = wizard.run()

    assert code == 0
    assert output.exists()


@pytest.mark.unit
def test_declining_to_write_returns_zero_without_a_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a, b = _two_sequential_files(tmp_path)
    output = tmp_path / "merged.gpx"

    _ScriptedPrompts(
        select=["append", "pause", "Garmin Edge 530", "gpx"],
        path=[str(a), str(b), "", str(output)],
        text=["", "", ""],  # timezone (skip), sport (skip), auto-lap (skip)
        confirm=[
            False,  # ignore big gaps?
            True,  # clean-up
            False,  # write it? -> no
        ],
    ).install(monkeypatch)

    code = wizard.run()

    assert code == 0
    assert not output.exists()


@pytest.mark.unit
def test_fit_input_is_accepted_by_file_validation(tmp_path: Path) -> None:
    # FIT is a headline read format, so the wizard must accept a .fit input.
    # _file_problem checks existence and extension, not parseability, so an
    # existing file with a .fit suffix is enough to prove the suffix is allowed.
    fit_file = tmp_path / "ride.fit"
    fit_file.write_bytes(b"")  # the bytes are not parsed by the validation step

    assert wizard._file_problem(fit_file) is None


@pytest.mark.unit
def test_cancelling_at_a_prompt_returns_usage_exit_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Pressing Ctrl-C makes questionary's ask() return None; the wizard treats
    # that as a clean cancel rather than crashing.
    monkeypatch.setattr(wizard.questionary, "select", lambda *a, **k: _Answer(None))

    code = wizard.run()

    assert code != 0


@pytest.mark.unit
def test_naming_a_profile_at_the_end_saves_the_general_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The final prompt names a profile; the wizard saves the merge's general,
    # file-independent settings under it (not the inputs or the output path).
    from gpsmerge import profiles

    home = tmp_path / "profiles"
    monkeypatch.setattr(wizard.profiles, "profiles_dir", lambda: home)

    a, b = _two_sequential_files(tmp_path)
    output = tmp_path / "merged.gpx"

    _ScriptedPrompts(
        select=["append", "pause", "Garmin Edge 530", "gpx"],
        path=[str(a), str(b), "", str(output)],
        # timezone, sport (skip), auto-lap (skip), then a profile name last.
        text=["Europe/Amsterdam", "", "", "commute"],
        confirm=[False, True, True],  # big gaps, clean-up, write it
    ).install(monkeypatch)

    code = wizard.run()

    assert code == 0
    saved = profiles.load_profile("commute", base=home)
    assert saved["timezone"] == "Europe/Amsterdam"
    assert saved["mode"] == "append"
    assert "inputs" not in saved
    assert "output" not in saved


def _write_power_gpx(path: Path, *, start_offset: int, level: int, count: int) -> Path:
    """Write a sequential, position-carrying GPX with a constant power level."""
    from datetime import UTC, datetime, timedelta

    from gpsmerge.io.gpx import write_gpx
    from gpsmerge.model import Activity, Segment, TrackPoint

    base = datetime(2026, 6, 4, 8, 0, 0, tzinfo=UTC)
    points = [
        TrackPoint(
            time=base + timedelta(seconds=start_offset + i),
            lat=52.0 + (start_offset + i) * 1e-5,
            lon=4.0,
            power=level,
        )
        for i in range(count)
    ]
    write_gpx(Activity(sport="cycling", segments=[Segment(points=points)]), path)
    return path


@pytest.mark.unit
def test_wizard_offers_workout_detection_when_power_is_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("numpy", reason="workout detection needs the numpy extra")

    a = _write_power_gpx(tmp_path / "a.gpx", start_offset=0, level=300, count=120)
    b = _write_power_gpx(tmp_path / "b.gpx", start_offset=130, level=180, count=120)
    output = tmp_path / "merged.tcx"

    seen: list[bool] = []
    real_run = wizard.run_pipeline

    def spy(settings: Any) -> Any:
        seen.append(settings.detect_workout)
        return real_run(settings)

    monkeypatch.setattr(wizard, "run_pipeline", spy)

    _ScriptedPrompts(
        select=["append", "pause", "Garmin Edge 530", "tcx"],
        path=[str(a), str(b), "", str(output)],
        # timezone (skip), sport (skip), then the profile name at the end (skip);
        # no auto-lap text prompt because workout detection is on.
        text=["", "", ""],
        confirm=[
            False,  # ignore big gaps?
            True,  # detect a workout? -> yes (this is the new gated prompt)
            True,  # clean-up
            True,  # write it?
        ],
    ).install(monkeypatch)

    code = wizard.run()

    assert code == 0
    assert output.exists()
    # The detect-workout flag reached both the preview and the final write run,
    # and the auto-lap text prompt was skipped (workout and auto-lap are exclusive).
    assert seen and all(seen)
    written = read(output)
    assert len(written.laps) >= 2
