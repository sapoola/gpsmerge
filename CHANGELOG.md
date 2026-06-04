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
- FIT (Garmin/ANT binary) reading: gpsmerge now reads `.fit` files (positions converted from semicircles, timestamps from the FIT epoch, enhanced altitude/speed preferred, device from the file's id message, pauses split at timer-stop events). This completes the v1.0 read contract — **read GPX/TCX/FIT, write GPX/TCX**. Writing FIT remains a later, experimental milestone.
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
