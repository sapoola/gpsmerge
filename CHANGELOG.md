# Changelog

All notable changes to this project are documented here. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Project scaffold: Poetry packaging, `src/` layout, MIT license, linting and type-checking (black, isort, ruff, mypy), pytest setup, CI on Python 3.11–3.14, and a `gpsmerge --version` entry point.
- Canonical data model (`TrackPoint`, `Segment`, `Device`, `Activity`) that every format converts to and from.
- GPX reading and writing with Garmin heart-rate/cadence/power/temperature extensions and device attribution, round-trip tested.
- Geometry helpers (`geo`): haversine distance, cumulative track distance, and overflow-safe FIT semicircle conversion.
- Time helpers (`timeutil`): timezone-aware UTC parsing and FIT-epoch conversion.
- Pure-Python linear resampling (`resample.lerp`) with gap-aware blanking, used by stacking later.
- Concept docs: `docs/data-model.md`, `docs/file-formats.md`, and `docs/concepts.md`.
- `gpsmerge run`: append sequential GPX files into one continuous activity.
- Device attribution: choose which input's recording device the merged file is recorded under (`--device-from`); defaults to the first input.
- Append options: gap policy (`--gap-policy pause|shift`), seam de-duplication, and distance recompute with `--ignore-gaps-over-meters`.
- Pre-merge validation with a clear exit-code taxonomy, a sanity-checked preview (with large-gap warnings), `--dry-run`, and `--json` output.
- `docs/merge-algorithms.md` explaining how append works.
- `gpsmerge run --mode stack`: overlay two recordings of the same ride and take a different sensor from each (GPS from a watch, power from a trainer).
- Per-channel source selection for stacking (`--position-from`, `--hr-from`, `--cadence-from`, `--power-from`, `--temp-from`) and `--max-gap-seconds`, with multi-source fallback and whole-number rounding of heart rate / cadence / power.
- Automatic mode detection (`--mode auto`, the default): stack vs append chosen from how much the recordings overlap; a genuinely ambiguous overlap asks you to pick.
- `docs/merge-algorithms.md` extended with the stacking and auto-mode sections.
- TCX (Garmin Training Center XML) reading and writing: gpsmerge now reads GPX or TCX and writes GPX or TCX (`--format tcx`). Temperature has no place in TCX and is dropped on write (documented).
- Interactive guided wizard: running `gpsmerge` with no arguments in a terminal walks you through choosing files, mode, per-channel and device sources, output format, and a sanity-checked preview before writing. Piped or in CI it prints a hint instead, so it never hangs.
- `gpsmerge examples` and `gpsmerge formats` quick-reference commands, and `docs/wizard.md`.
- FIT (Garmin/ANT binary) reading: gpsmerge now reads `.fit` files (positions converted from semicircles, timestamps from the FIT epoch, enhanced altitude/speed preferred, device from the file's id message, pauses split at timer-stop events). This completes the v1.0 read contract — **read GPX/TCX/FIT, write GPX/TCX**. (Experimental FIT *writing* followed — see below.)
- Tests that pin the GPX/TCX/Garmin-extension XML namespace identifiers, so an accidental change (they are fixed identifiers, not fetchable URLs) fails loudly.
- `--timezone` flag and a wizard prompt that show the preview's times in your local zone (display only — the written file stays UTC). An IANA name like `Europe/Amsterdam` auto-resolves summer time; a fixed offset like `+02:00` also works.
- `xsi:schemaLocation` on written GPX and TCX roots, pointing at the resolvable Garmin/topografix schema documents, matching real Garmin output.
- Indoor (no-GPS) merges now work: the recorded distance is kept instead of being zeroed by a recompute that had no positions, and when no file has GPS the data-richer file becomes the master (so its timeline and distance are kept) regardless of input order.
- Writing a position-less (indoor) activity to GPX is refused with guidance to use `--format tcx`, since GPX trackpoints require coordinates and cannot store distance.
- FIT device names are tidied for display (e.g. `stages_cycling 1` becomes `Stages Cycling`, `garmin edge_530` becomes `Garmin Edge 530`); the raw manufacturer/product values are kept for fidelity.
- Config profiles: save the general, file-independent settings you chose with `--save-profile <name>` (or from the wizard) and reuse them with `--profile <name>`; manage them with `gpsmerge profile list|show|rm`. A profile never stores input/output paths, and an explicit flag always overrides a profile.
- Post-merge transforms: `--shift` adds a signed duration to every timestamp (fix a wrong device clock); `--trim-start`/`--trim-end` crop to a sub-range and re-base distance to start at zero; `--smooth-elevation <window>` runs a centred moving average over noisy GPS elevation; `--insert-stopped-time` turns long non-moving gaps into pauses so they stop counting as moving time.
- A polished guided wizard: an ASCII banner on launch, numbered section steps with friendly plain-language prompts, an encouraging auto-detect summary, and a green "Done" panel summarising the merged file with a tip to save the run as a profile. The banner appears only in the interactive wizard, never when piped or in CI.
- `--out` is now optional for `--dry-run` (a preview writes nothing, so it no longer needs an output path); a real write without `--out` is a clear usage error.
- Lap support: gpsmerge reads laps from TCX and FIT and writes them to TCX, as a boundaries-only overlay (start, end, trigger) kept separate from pause segments — so a pause inside a lap and a lap inside a segment both survive. Laps carry through append (concatenated, time-shifted to match), stack (from the master), and the `--shift`/`--trim` transforms; per-lap totals are recomputed on write so they never go stale. GPX has no lap concept, so laps are dropped on GPX write.
- Clock-skew alignment for stacking (`--align-clocks`, and a wizard prompt): measures a donor device's clock offset by cross-correlating a channel both files record (speed, power, heart rate, cadence, or elevation) and shifts the donor's timestamps to match the master before borrowing its channels, so a donor clock that runs a few seconds fast or slow no longer smears the merged data. Off by default; the master timeline is never moved, and a weak, flat, or ambiguous correlation leaves the donor untouched.
- Distance auto-lap (`--auto-lap <distance>`, and a wizard prompt): re-lap a merged activity into even distance splits — a fresh lap every `1km`, `500m`, `0.5mi`, `1mi`, etc. — replacing whatever laps the source carried. Each lap closes as the running distance crosses the next interval mark (using the recorded distance channel when it is present and monotonic, else a recompute from GPS positions); completed laps get the `distance` trigger and the leftover a `session_end` one, and an activity shorter than one interval is a single lap. Runs last among the transforms, so the laps reflect the final cropped and re-based track. An activity with no distance and no positions is refused up front with a clear message. Laps are written to TCX and FIT; GPX has no lap concept, so they are dropped on GPX write. (The shared "boundaries → laps" plumbing this adds is reused by a later workout detector.)
- Workout detection (`--detect-workout`, **BETA**, and a wizard prompt): auto-detect a structured interval workout from the merged activity's effort channel and write it back as laps plus a one-line summary. The channel is chosen automatically — power for rides, speed for runs — with no flag to force it; an activity with neither a varying power nor a varying speed channel is refused with a clear message. The heuristic resamples the chosen channel to 1 Hz, smooths it, and segments it into held efforts, counting a step boundary only when the level shifts by at least `max(floor, 0.15 × ride_average)`. Scaling the threshold to the ride's *own* average (one cheap mean, no FTP, no user input) self-calibrates: a ~110 W ride treats a 100→130 W step as a real interval, while a ~350 W ride treats the same +30 W as within-block noise. `--workout-min-seconds` (default 10) sets the shortest effort kept as its own lap, but a genuinely distinct short rep survives. The summary renders power in watts (rounded to 5 W) and speed as pace per km. Runs last among the transforms; mutually exclusive with `--auto-lap` (both set the laps). Needs the optional `numpy` dependency, installed with the `workout` extra (`pip install 'gpsmerge[workout]'`); imported lazily so the core install stays numpy-free. Choosing it prints a one-line BETA heads-up on stderr. Best on structured/trainer/interval workouts; rougher on smooth ramps and unstructured outdoor rides. See `docs/workout-detection.md`.
- FIT writing (`--format fit`, **experimental**): gpsmerge can now write the binary FIT format your head unit records to, not just read it. The output is a structurally complete activity (file id, optional sport, timer-bracketed records, per-segment timer stops so pauses survive, laps, and a session/activity summary with totals recomputed on write) and is round-trip tested against gpsmerge's own reader. Writing FIT needs the optional `fit-tool` dependency, installed with the `fit` extra (`pip install 'gpsmerge[fit]'`); reading FIT is unaffected. Choosing FIT prints a one-line "experimental" heads-up on stderr, and the wizard labels the FIT output choice as experimental. Known, documented losses: sub-second times truncate to whole seconds (FIT record resolution); temperature rounds to whole degrees (FIT stores it as a 1 °C signed byte); device attribution is best-effort (serial, creation time, and name survive; manufacturer maps back only when its name matches, else is omitted); a sport with no FIT enum is omitted rather than guessed; and a reading equal to FIT's "invalid" sentinel (heart rate/cadence 255, power 65535, temperature 127) reads back as absent. A channel value beyond what FIT can store at all is refused with a clear error naming the point rather than a raw crash, so the same activity that exports cleanly to GPX/TCX fails loudly on `--format fit` instead of writing a corrupt file.

<!--
Release reference links. Nothing is released yet, so [Unreleased] points at the
full commit history. When the first version is tagged, change [Unreleased] to a
compare link against that tag and add a line for the release, for example:

[Unreleased]: https://github.com/sapoola/gpsmerge/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/sapoola/gpsmerge/releases/tag/v0.1.0
-->

[Unreleased]: https://github.com/sapoola/gpsmerge/commits/main
