# Contributing to gpsmerge

## Setup

This project uses [uv](https://docs.astral.sh/uv/) (build backend: `uv_build`). To get a working environment:

```bash
uv sync
uv run gpsmerge --version
```

You need uv 0.13.9 or newer (`uv self update`, or `brew upgrade uv`); older releases cannot build the sdist and `pyproject.toml` says so up front.

`uv sync` installs the project, its dev tools and the test dependencies into `.venv`, exactly as pinned in `uv.lock`. Commit `uv.lock` whenever you change a dependency (`uv add`, `uv remove`, or `uv lock` after editing `pyproject.toml`).

Install the git hooks once so the formatter and linters run on every commit:

```bash
uv run pre-commit install
```

## The quality bar

Before you push, all of these must be clean:

```bash
uv run black --check .
uv run isort --check-only .
uv run ruff check .
uv run mypy src
uv run pytest
```

(`uv run pre-commit run --all-files` runs the first four for you.)

## The simplicity rule

The most important rule in this codebase: **write the boring, obvious version**. Code is read far more often than it is written, and the next person to touch it may be junior. Reach for an abstraction (a base class, a registry, a config flag) only when real, present duplication or change-pressure justifies it, never "in case it's needed later". You can always extract an abstraction from working code; you can rarely simplify a speculative one.

Concretely:

- Functions do one thing and are named for what they do.
- Every public function has a docstring describing its contract.
- Comments explain *why*, never *what*.
- No magic numbers: name your constants.
- Tests come first, then the least code that makes them pass.

## Adding a new file format

Formats are plain functions, not a plugin system. To add one (say, KML):

1. Write `read_kml(path) -> Activity` and/or `write_kml(activity, path)` in `src/gpsmerge/kml.py`.
2. Add one branch to the `if/elif` in `read.py` / `write.py`.
3. Add round-trip tests in `tests/`.

That is the whole process: there is no registry to update and no base class to subclass.
