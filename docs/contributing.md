# Contributing to gpsmerge

## Setup

This project uses [Poetry](https://python-poetry.org/). To get a working environment:

```bash
poetry install
poetry run gpsmerge --version
```

Install the git hooks once so the formatter and linters run on every commit:

```bash
poetry run pre-commit install
```

## The quality bar

Before you push, all of these must be clean:

```bash
poetry run black --check .
poetry run isort --check-only .
poetry run ruff check .
poetry run mypy src
poetry run pytest
```

(`poetry run pre-commit run --all-files` runs the first four for you.)

## The simplicity rule

The most important rule in this codebase: **write the boring, obvious version**. Code is read far more often than it is written, and the next person to touch it may be junior. Reach for an abstraction — a base class, a registry, a config flag — only when real, present duplication or change-pressure justifies it, never "in case it's needed later". You can always extract an abstraction from working code; you can rarely simplify a speculative one.

Concretely:

- Functions do one thing and are named for what they do.
- Every public function has a docstring describing its contract.
- Comments explain *why*, never *what*.
- No magic numbers — name your constants.
- Tests come first, then the least code that makes them pass.

## Adding a new file format

Formats are plain functions, not a plugin system. To add one (say, KML):

1. Write `read_kml(path) -> Activity` and/or `write_kml(activity, path)` in `src/gpsmerge/kml.py`.
2. Add one branch to the `if/elif` in `read.py` / `write.py`.
3. Add round-trip tests in `tests/`.

That is the whole process — there is no registry to update and no base class to subclass.
