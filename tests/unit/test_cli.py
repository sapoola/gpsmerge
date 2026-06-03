"""Tests for the gpsmerge command-line entry point."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from gpsmerge import __version__
from gpsmerge.cli import app

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
