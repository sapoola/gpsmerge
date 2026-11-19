"""Tests for the gpsmerge ASCII banner.

The banner is pure console output, so each test renders it to a recording
:class:`rich.console.Console` and inspects the captured text. That confirms it
prints without error and carries the wordmark and the tagline.
"""

from __future__ import annotations

import pytest
from rich.console import Console

from gpsmerge.cli import banner


@pytest.mark.unit
def test_render_prints_the_wordmark_and_tagline_without_error() -> None:
    console = Console(record=True, width=80)

    banner.render(console)

    text = console.export_text()
    # The wordmark is ASCII art, so assert on the tagline and trail, which carry
    # readable words rather than figlet glyphs.
    assert "merge GPS activity files" in text
    assert "offline" in text
    assert "private" in text


@pytest.mark.unit
def test_render_includes_the_version() -> None:
    from gpsmerge import __version__

    console = Console(record=True, width=80)

    banner.render(console)

    assert __version__ in console.export_text()
