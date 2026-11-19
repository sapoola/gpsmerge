<!--
Thanks for the change! Keep the PR title in Conventional Commits form
(feat:, fix:, docs:, refactor:, perf:, test:, ci:, chore:) — it becomes the
squash-merge commit on main. Area labels (ci, tests, docs, core,
dependencies) are applied automatically from the files you touched.
-->

## What & why

<!-- What does this PR change, and why? Link any issue, e.g. "Closes #12". -->

## Type of change

<!-- Tick all that apply. -->

- [ ] feat — new user-facing capability
- [ ] fix — bug fix
- [ ] docs — documentation only
- [ ] refactor — no behaviour change
- [ ] perf — performance
- [ ] test — tests only
- [ ] ci / chore — tooling, CI, or dependencies
- [ ] breaking change

## How it was tested

<!-- Commands run, fixtures used, manual checks. e.g. a merge over two sample files. -->

## Checklist

- [ ] `poetry run pytest` passes
- [ ] `black`, `isort`, `ruff`, and `mypy src` are clean (or `pre-commit run --all-files`)
- [ ] Docs updated (`docs/`, `README.md`) if behaviour changed
- [ ] `CHANGELOG.md` updated under `[Unreleased]` for any user-facing change
- [ ] PR title follows Conventional Commits
