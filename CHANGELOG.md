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
