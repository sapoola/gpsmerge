# The data model: one shape everything converts to

gpsmerge reads several file formats, merges activities, and writes them back out. The piece that makes that tractable is a single small data model in `src/gpsmerge/model.py` that sits in the middle. Every reader converts *into* it, every merge operates *only on* it, and every writer converts *out of* it. This page explains what is in the model and, just as importantly, why it is shaped the way it is.

## Why one canonical model

Imagine we skipped the shared model and converted formats directly to each other. With GPX, TCX, and FIT that is GPX-to-TCX, GPX-to-FIT, TCX-to-GPX, TCX-to-FIT, and so on — every pair, in both directions. Add a fourth format and the pairwise count grows uncomfortably. That is the N x N problem: lots of converters, each one a place for bugs, each new format multiplying the work.

The fix is a hub. Each format gets **one reader** (format to model) and **one writer** (model to format). To support N formats you write N readers and N writers — `2N` pieces, not `N x N` — and the merge logic is written once against the model, never against any file format. A FIT quirk like semicircle coordinates is dealt with inside the FIT reader and never leaks out; the merge code only ever sees plain `TrackPoint`s.

This is the single most important idea in the codebase. Everything below serves it.

## The two invariants

Two rules hold *everywhere* in the model, and the merge and writing logic lean on them. Break either one and code elsewhere quietly does the wrong thing.

1. **An absent value is `None` — never `0`, never a sentinel.** If a device did not record heart rate, the `hr` field is `None`, not `0` bpm. This matters because `0` is a real, valid reading for some channels (zero power when you are coasting, zero cadence when you stop pedalling). If "missing" and "genuinely zero" looked the same, a merge could not tell "this file has no power data, borrow it from the other" from "this rider was coasting here". `None` keeps that distinction honest, and writers honour it by simply omitting absent channels from the output rather than writing zeros.

2. **Every timestamp is timezone-aware UTC.** No naive datetimes, no local time, no surprises when two files were recorded in different time zones. Readers normalise to UTC on the way in (a file that omits a zone is taken to mean UTC); the time helpers in `timeutil.py` only ever return UTC. Because aligning two recordings is fundamentally about matching instants in time, a single shared clock is non-negotiable — stacking would misalign and appending would order things wrongly if time zones drifted in.

## The four classes

The model is four small dataclasses. Read them top to bottom and there is nothing hidden.

### TrackPoint — one recorded instant

A `TrackPoint` is a single moment: where you were and what the sensors read. It has a `time`, optional position (`lat`, `lon`, `ele`), optional `distance` along the activity, and the fitness channels `hr`, `cad`, `power`, `temp`, and `speed`. Every field except `time` is optional, because different devices record different channels — and, per invariant one, an unrecorded channel is `None`.

### Segment — a continuous run of recording, bounded by pauses

A `Segment` is just a list of `TrackPoint`s, but what it *represents* is important: **one continuous stretch of recording, with a pause at each end.** A new segment begins wherever recording stopped and restarted — a red light you waited at, a coffee stop, a device switched off and on. The boundary between two segments *is* a pause.

That boundary is not bookkeeping; it carries meaning. It is what lets a writer distinguish moving time from elapsed time, and it is how an append (see `concepts.md`) preserves the real gap between "battery died" and "phone started" instead of drawing a false straight line across it. In GPX terms a `Segment` maps directly to a `<trkseg>`. The `Activity.points()` helper flattens all segments into one time-ordered stream when you genuinely do not care where the pauses are.

### Device — who recorded it, for attribution

A `Device` describes the recording device: a human-readable `name` (for example `"Garmin Edge 530"`), and the more structured `manufacturer`, `product`, `serial_number`, and `software_version` that mainly come from FIT.

Its job is **device attribution**. When two files are merged, the output is recorded under one chosen device's identity — you might stack power from a Stages head unit onto a route from a Garmin Edge, and want the result to say it came from the Edge. The device is read from the GPX `creator` attribute, the TCX `<Creator>` element, or the FIT `file_id` message, and written back out the same way, so the merged file presents a single coherent identity.

### Activity — the whole thing we read, merge, and write

An `Activity` ties it together: an optional `sport` and `name`, the `Device` that recorded it, the list of `Segment`s, a `time_created`, and `source_path` / `source_format` recording where it came from. This is the unit the whole tool operates on — readers return an `Activity`, merges take and return `Activity`s, writers consume one.

## What is deliberately left out, and why

You might expect a model like this to also carry laps, route waypoints, GPS accuracy, lap-level summaries, or a bag of arbitrary device-specific fields. They are **deliberately absent**, and that is a design choice, not an oversight.

The rule is: model a concept when a feature actually needs it, not because a file format happens to have it. Stacking and appending — the two jobs gpsmerge does — work on timestamped points, segment boundaries, and device identity. They do not need laps or waypoints, so adding them now would be speculative weight: more fields to keep consistent across every reader and writer, more invariants to defend, more for the next person to read past, all to support nothing that exists yet.

This keeps the model small enough to hold in your head and the readers and writers honest. When a real milestone needs laps (TCX's lap structure is the obvious candidate, since TCX requires it — see `file-formats.md`), the field gets added then, with a reader and writer that exercise it and tests that prove it. Extracting structure from working code is easy; carrying speculative structure forever is not. The model's own module docstring states this intent directly, so the next engineer does not "helpfully" add the fields back.
