# gpsmerge documentation

These docs explain the *concepts* behind gpsmerge in plain language. They are written so that someone new to the codebase — or to GPS files — can understand what is going on without reading every line of code. The precise, function-level reference lives in the docstrings in the source.

## Start here

- [`concepts.md`](concepts.md) — what "stack" and "append" mean, with real examples.
- [`file-formats.md`](file-formats.md) — a gentle primer on GPX, TCX, and FIT and what each can carry.
- [`data-model.md`](data-model.md) — the one internal model every format converts to and from.
- [`merge-algorithms.md`](merge-algorithms.md) — how stacking and appending actually work, step by step.
- [`wizard.md`](wizard.md) — the interactive guided wizard you get when you run `gpsmerge` with no arguments.
- [`transforms.md`](transforms.md) — the optional fixes you can apply on the way through: time shift, trim, elevation smoothing, and stopped-time insertion.
- [`profiles.md`](profiles.md) — saving a set of merge settings under a name and reusing it with `--profile`.
- [`contributing.md`](contributing.md) — running the tests, the formatter, and the type-checker.

## Guiding principle

The code is written to be read. Prefer the boring, obvious version; reach for an abstraction only when real, present duplication or change-pressure justifies it. If something here is hard to follow, that is a bug in the docs — please open an issue.
