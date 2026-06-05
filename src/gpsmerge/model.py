"""The canonical data model that every file format converts to and from.

Parsers (``read_gpx``, ``read_tcx``, ``read_fit``) turn a file into an
:class:`Activity`; the merge functions operate only on :class:`Activity`; writers
turn an :class:`Activity` back into a file.

Two rules hold everywhere in this model and are relied on by the merge logic:

- An absent value is ``None`` -- never ``0`` and never a sentinel. A missing heart
  rate is ``None``, not ``0`` bpm.
- Every timestamp is timezone-aware UTC.

Laps are a boundaries-only overlay (see :class:`Lap`). See ``docs/data-model.md``.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime


@dataclass(slots=True)
class TrackPoint:
    """A single recorded instant: where you were and what the sensors read.

    Every field except ``time`` is optional, because different devices and file
    formats record different channels. An absent channel is ``None``.

    Attributes
    ----------
    time : datetime or None
        Timezone-aware UTC timestamp. ``None`` only for course/route points that
        carry no time.
    lat, lon : float or None
        Position in decimal degrees (WGS84).
    ele : float or None
        Elevation in metres.
    distance : float or None
        Cumulative distance from the start of the activity, in metres, if the file
        recorded it.
    hr : int or None
        Heart rate in beats per minute.
    cad : int or None
        Cadence -- revolutions per minute (cycling) or steps per minute (running).
    power : int or None
        Power in watts.
    temp : float or None
        Temperature in degrees Celsius.
    speed : float or None
        Instantaneous speed in metres per second, if the file recorded it.
    """

    time: datetime | None
    lat: float | None = None
    lon: float | None = None
    ele: float | None = None
    distance: float | None = None
    hr: int | None = None
    cad: int | None = None
    power: int | None = None
    temp: float | None = None
    speed: float | None = None


@dataclass(slots=True)
class Segment:
    """One continuous run of recording.

    A new segment starts wherever recording paused -- a stop light, a coffee stop,
    or a device that was turned off and on again. Keeping pauses as segment
    boundaries is what lets the writers tell moving time from elapsed time. A
    segment maps to a GPX ``<trkseg>``.
    """

    points: list[TrackPoint] = field(default_factory=list)


@dataclass(slots=True)
class Lap:
    """A labelled span of an activity -- an interval or an auto-lap.

    A lap is an overlay on the timeline, kept separate from segment (pause)
    boundaries: one continuous segment can hold many laps, and a pause is not
    necessarily a lap boundary, so laps and segments are independent lists. Only
    the span and the trigger are stored; per-lap summary totals (time, distance,
    averages) are recomputed from the points that fall in the span when writing,
    so they always match the (possibly merged) data instead of going stale.

    Attributes
    ----------
    start_time : datetime
        Timezone-aware UTC start of the lap.
    end_time : datetime or None
        Timezone-aware UTC end of the lap; ``None`` runs to the activity's end.
    trigger : str or None
        Why the lap ended, when the file records it: ``"manual"``, ``"distance"``,
        ``"time"``, ``"position"``, or ``"session_end"``.
    """

    start_time: datetime
    end_time: datetime | None = None
    trigger: str | None = None


@dataclass(slots=True)
class Device:
    """The device that recorded an activity, used for device attribution.

    When two files are merged the output is recorded under one chosen device's
    identity (for example a Garmin Edge 530 rather than a Stages Dash M50). This is
    read from the GPX ``creator`` attribute, the TCX ``<Creator>`` element, or the
    FIT ``file_id`` message, and written back out the same way.

    Attributes
    ----------
    name : str or None
        Human-readable device name, e.g. ``"Garmin Edge 530"``.
    manufacturer, product : str or None
        Structured identity, when the source file provides it (mainly FIT).
    serial_number : int or None
        Device serial number, when available.
    software_version : str or None
        Firmware/software version string, when available.
    """

    name: str | None = None
    manufacturer: str | None = None
    product: str | None = None
    serial_number: int | None = None
    software_version: str | None = None


@dataclass(slots=True)
class Activity:
    """A whole recorded activity: the thing we read, merge, and write.

    Attributes
    ----------
    sport : str or None
        Canonical lowercase sport, e.g. ``"cycling"`` or ``"running"``.
    name : str or None
        Activity name/title, if the file has one.
    device : Device
        Who recorded it (see :class:`Device`).
    segments : list of Segment
        The recording, split into continuous runs at every pause.
    laps : list of Lap
        Lap markers over the timeline (empty when the source has no laps, e.g.
        GPX). Independent of segment boundaries.
    time_created : datetime or None
        When the file says the activity was created, if recorded.
    source_path : str or None
        Path the activity was read from, for messages and defaults.
    source_format : str or None
        One of ``"gpx"``, ``"tcx"``, ``"fit"``.
    """

    sport: str | None = None
    name: str | None = None
    device: Device = field(default_factory=Device)
    segments: list[Segment] = field(default_factory=list)
    laps: list[Lap] = field(default_factory=list)
    time_created: datetime | None = None
    source_path: str | None = None
    source_format: str | None = None

    def points(self) -> Iterator[TrackPoint]:
        """Iterate every track point across all segments, in recorded order.

        Returns
        -------
        Iterator of TrackPoint
            A flat, time-ordered view over the activity, ignoring segment
            boundaries. Use this when you want the points and do not care where the
            pauses are.
        """
        for segment in self.segments:
            yield from segment.points

    def channel_series(self, channel: str) -> list[tuple[float, float]]:
        """Return sorted ``(time-seconds, value)`` samples for one channel.

        Points with no time or no value for the channel are skipped. The merge and
        the clock-skew alignment both use this to resample a channel onto another
        activity's timeline.

        Parameters
        ----------
        channel : str
            A channel attribute name, e.g. ``"hr"`` or ``"power"``.

        Returns
        -------
        list of (float, float)
            ``(POSIX seconds, value)`` pairs, sorted by time.
        """
        samples = [
            (point.time.timestamp(), float(getattr(point, channel)))
            for point in self.points()
            if point.time is not None and getattr(point, channel) is not None
        ]
        samples.sort()
        return samples
