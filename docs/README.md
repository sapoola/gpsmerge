# gpsmerge documentation

These docs explain the *concepts* behind gpsmerge in plain language. They are written so that someone new to the codebase — or to GPS files — can understand what is going on without reading every line of code. The precise, function-level reference lives in the docstrings in the source.

## Start here

- [`concepts.md`](concepts.md) — what "stack" and "append" mean, with real examples. *(Coming in M1–M2.)*
- [`file-formats.md`](file-formats.md) — a gentle primer on GPX, TCX, and FIT. *(Coming in M1.)*
- [`data-model.md`](data-model.md) — the one internal model every format converts to and from. *(Coming in M1.)*
- [`architecture.md`](architecture.md) — how a file goes in and a merged file comes out. *(Coming in M2.)*
- [`merge-algorithms.md`](merge-algorithms.md) — how stacking and appending actually work. *(Coming in M2–M3.)*
- [`device-attribution.md`](device-attribution.md) — choosing which device the merged file is recorded under. *(Coming in M4.)*
- [`contributing.md`](contributing.md) — running the tests, the formatter, and the type-checker. *(Coming in M0–M1.)*

## Guiding principle

The code is written to be read. Prefer the boring, obvious version; reach for an abstraction only when real, present duplication or change-pressure justifies it. If something here is hard to follow, that is a bug in the docs — please open an issue.
