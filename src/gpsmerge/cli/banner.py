"""The gpsmerge ASCII banner shown when the guided wizard starts.

The wordmark was generated with pyfiglet's "standard" font and hardcoded here.
:func:`render` prints it with a cool-to-warm colour gradient and a tagline.
"""

from __future__ import annotations

from rich.console import Console
from rich.text import Text

from gpsmerge import __version__

_ART = r"""  __ _ _ __  ___ _ __ ___   ___ _ __ __ _  ___
 / _` | '_ \/ __| '_ ` _ \ / _ \ '__/ _` |/ _ \
| (_| | |_) \__ \ | | | | |  __/ | | (_| |  __/
 \__, | .__/|___/_| |_| |_|\___|_|  \__, |\___|
 |___/|_|                           |___/"""

# One colour per wordmark line, cool to warm, for a subtle gradient.
_LINE_COLOURS = ("bright_cyan", "cyan", "green", "bright_green", "yellow")

# A waypoint trail under the wordmark keeps the GPS theme.
_TRAIL = "  o---o---o---o---o---o---o---o---o---o---o---o"

_TAGLINE = "merge GPS activity files  ·  offline  ·  private"


def render(console: Console) -> None:
    """Print the gpsmerge banner with a colour gradient and a tagline.

    Parameters
    ----------
    console : Console
        The rich console to print to.
    """
    wordmark = Text()
    for line, colour in zip(_ART.splitlines(), _LINE_COLOURS):
        wordmark.append(line + "\n", style=f"bold {colour}")
    console.print(wordmark)
    console.print(f"[dim cyan]{_TRAIL}[/dim cyan]")
    console.print(f"  [dim]{_TAGLINE}[/dim]   [dim cyan]v{__version__}[/dim cyan]\n")
