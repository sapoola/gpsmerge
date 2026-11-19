# Contributing to gpsmerge

Thanks for your interest in improving gpsmerge. The full guide (environment setup, how the code is laid out, and how to add a new file format) lives in [`docs/contributing.md`](docs/contributing.md). This page is the short version.

## The simplicity bar

The most important rule in this codebase: **write the boring, obvious version**. Code is read far more often than it is written, and the next person to touch it may be junior. Reach for an abstraction (a base class, a registry, a plugin system, a config flag) only when real, present duplication or change-pressure justifies it, never "in case it's needed later". Functions do one thing and are named for what they do, every public function has a docstring, comments explain *why* rather than *what*, and there are no magic numbers. Tests come first, then the least code that makes them pass.

## The quality gate

Before you push, all of these must be clean:

```bash
poetry run black --check .
poetry run isort --check-only .
poetry run ruff check .
poetry run mypy src
poetry run pytest
```

Install the git hooks once so the first four run automatically on every commit:

```bash
poetry run pre-commit install
```

To report a security issue, see [`SECURITY.md`](SECURITY.md) instead of opening a public issue.
