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
