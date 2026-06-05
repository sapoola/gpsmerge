# Project status & roadmap

A snapshot of where gpsmerge is and what's next, written so the project can be picked up cold. Last updated 2026-06-04.

## Snapshot

gpsmerge is an offline, local-first CLI that merges GPX, TCX, and FIT activity files — an on-device reimplementation of gotoes.org's merge tool, with no upload, no account, and no retention. The engineering is essentially feature-complete: it reads all three formats, writes GPX/TCX (and FIT experimentally), does the two core merges (stack and append) with auto-detection, and carries a stack of analysis and clean-up features on top. What remains is mostly **release and distribution**, plus a backlog of "later" extras.

State: 21 commits on `main`, **397 tests green**, `black`/`isort`/`ruff`/`mypy` all clean, Python 3.11–3.14. Local git only — **no remote yet, nothing published**. Repo lives at `~/personal/gpsmerge` under the personal git identity.

## What works today

**Read / write**

- Read GPX, TCX, FIT (semicircles, FIT epoch, enhanced fields, device from file id, pauses from timer-stops).
- Write GPX, TCX, and FIT (FIT is experimental, behind the `gpsmerge[fit]` extra).
- One canonical `Activity` model in the middle; N readers + N writers, never N×N.

**Merging**

- `stack` — overlay two recordings of the same ride, taking a different sensor channel from each (GPS from a watch, power from a trainer). Per-channel source selection (`--position-from`, `--hr-from`, `--cadence-from`, `--power-from`, `--temp-from`).
- `append` — join sequential recordings (battery died mid-ride). Gap policy (`pause`/`shift`), seam de-duplication, big-gap handling.
- `auto` mode detection (the default), with a clear "ambiguous → ask" path.
- **Clock-skew alignment** (`--align-clocks`) — corrects a donor device whose clock is a few seconds off, by cross-correlating a shared channel.
- **Device attribution** (`--device-from`) — choose which source device the merged file is recorded under.

**Analysis & clean-up**

- Transforms: `--shift`, `--trim-start`/`--trim-end`, `--smooth-elevation`, `--insert-stopped-time`.
- **Lap model** — read laps from TCX/FIT, write to TCX/FIT (boundaries-only overlay, totals recomputed on write).
- **Distance auto-lap** (`--auto-lap 1km`) — re-lap into even distance splits.
- **Workout detection (BETA)** (`--detect-workout`) — auto-detect the interval structure in the effort channel (power for rides, speed→pace for runs), with the significance threshold scaled to the ride's own average effort. Needs the `gpsmerge[workout]` extra (numpy). `--workout-min-seconds` is the one coarseness knob.

**UX**

- Guided interactive wizard (bare `gpsmerge`) with an ASCII banner, recovery branches, and a sanity-checked preview.
- Config profiles (`--save-profile`/`--profile`, `gpsmerge profile list|show|rm`).
- `--timezone` display, `--dry-run`, `--json`, a clear exit-code taxonomy, and `gpsmerge examples` / `gpsmerge formats`.

## Milestone status

Milestones are from the build plan (`~/.claude/plans/graceful-wondering-spindle.md`, section 10).

| Milestone | Scope | Status |
|---|---|---|
| M0 | Scaffold (Poetry, src layout, MIT, lint/type, CI config, `--version`) | ✅ Done |
| M1 | Model + GPX round-trip + device read/write | ✅ Done |
| M2 | APPEND end-to-end | ✅ Done |
| M3 | STACK + auto-detect | ✅ Done |
| M4 | TCX read/write + wizard + device selection | ✅ Done |
| M5 | FIT read + cross-format matrix (GA read contract) | ✅ Done |
| M6 | v1.0 *features* (laps, profiles, clock-align, smoothing, stopped-time, time-shift) | ✅ Done |
| M6 | v1.0 *release* (GitHub repo, PyPI publish, Homebrew tap, version tag) | ❌ Not started (deliberate) |
| M7 | FIT-write (experimental) | ✅ Code done; live upload-acceptance gate outstanding |
| M8+ | Later tier (ZIP, CSV/KML, MultiSport, grade models, SRTM, FIT repair) | ❌ Not started |

Beyond the original plan, two features were added this session: **distance auto-lap** and the **BETA workout detector** (the latter is a genuinely new USP — auto-detecting a structured workout from power or speed).

## On the horizon

### 1. Before the first public release (the immediate next step)

This is the main outstanding work — everything buildable-and-verifiable offline is done.

- [ ] Create the repo under the **personal** GitHub account (`github.com/sapoola/gpsmerge`) and push. The first push activates CI (`.github/workflows/ci.yml`), which has never actually run (no remote yet).
- [ ] Replace the placeholder security contact in `SECURITY.md` (`security@gpsmerge.invalid`) with a real one — a personal email or a GitHub private security advisory link.
- [ ] Decide the first tag: **v0.1.0** (honest beta — FIT-write and workout detection are experimental/beta) vs **v1.0.0**. v0.1.0 is the safer call. Currently deliberately uncut.
- [ ] Configure **PyPI Trusted Publishing** (OIDC). `.github/workflows/release.yml` is dormant and tag-triggered; it needs the PyPI side set up. Do TestPyPI first.
- [ ] Set up the **Homebrew tap** (`homebrew-gpsmerge`, `brew update-python-resources`, pin `python@3.13`). `lxml` is the only compiled wheel, so resource maintenance is light.
- [ ] Install smoke test: `poetry build` then `pipx install dist/*.whl`, run `--version` + a two-fixture merge.

### 2. Near-term enhancements (polish; surfaced in reviews)

- [ ] Workout detector: group repeated reps in the **summary** (`8× 0:30 @ 375 W / 0:15 @ 200 W`) so a dense HIIT ride reads cleanly even when the lap overlay is fine-grained. (The `--workout-min-seconds` knob already lets you coarsen the laps; this is about the human-readable line.)
- [ ] **Time-based auto-lap** (`--auto-lap 5min`) — a near-trivial follow-on to distance auto-lap, sharing the same boundaries→laps helper.
- [ ] **Smart Trim** — auto-detect and trim idle time at the start/end of a ride. Listed under M6 in the plan but built only as manual range `--trim-start`/`--trim-end`.
- [ ] Workout detection: optional sport-aware channel choice (prefer speed for runs even when a power meter is present). Currently power-first always; fine for the common case.
- [ ] FIT-write: a live Strava/Garmin **upload-acceptance gate** — M7's remaining "done" bar. Inherently manual/external (can't run in CI); a written `.fit` is round-trip tested against our own reader but not yet proven against a real importer.

### 3. Later tier (M8+ backlog)

- [ ] ZIP archive input.
- [ ] CSV / KML read and write.
- [ ] Auto-lap by **position** (lap each time you cross a point).
- [ ] MultiSport / brick activities.
- [ ] Desired-average-speed grade model.
- [ ] SRTM elevation fill.
- [ ] Power-adjust / FIT repair.

## Known limitations (by design, documented)

- **FIT-write (experimental):** sub-second times and temperature quantise to FIT's resolution; device attribution is best-effort; a reading equal to FIT's "invalid" sentinel reads back absent; a long recording dropout can inflate a neighbouring interval's duration. See `docs/file-formats.md`.
- **Workout detection (BETA):** a heuristic, not a contract. Shines on structured/trainer/interval sessions; rough on smooth ramps, unstructured outdoor riding, and long sensor dropouts. A dense HIIT ride produces many (faithful) laps — raise `--workout-min-seconds` to coarsen. See `docs/workout-detection.md`.
- **GPX:** has no lap concept (laps dropped on GPX write) and GPX 1.1 has no trackpoint speed.
- **Optional extras:** FIT writing needs `gpsmerge[fit]` (fit-tool); workout detection needs `gpsmerge[workout]` (numpy). The core install needs neither.

## How to resume

- **Repo:** `~/personal/gpsmerge` (personal git identity via the `includeIf "gitdir:~/personal/"` rule). No remote yet.
- **Build plan:** `~/.claude/plans/graceful-wondering-spindle.md` (the original, with the full feature/release tiers).
- **Run the gate:** `poetry run pytest -q` then `poetry run black --check . && poetry run isort --check . && poetry run ruff check . && poetry run mypy src`.
- **Try it:** `poetry run gpsmerge --help`, or bare `poetry run gpsmerge` for the wizard. `poetry install --extras "fit workout"` to enable FIT writing and workout detection locally.
- **Orientation docs:** `docs/concepts.md` (stack vs append), `docs/data-model.md`, `docs/file-formats.md`, `docs/merge-algorithms.md`, `docs/wizard.md`, `docs/transforms.md`, `docs/profiles.md`, `docs/workout-detection.md`, `docs/contributing.md`.
- **Changelog:** `CHANGELOG.md` (everything sits under `[Unreleased]` until the first tag).

**The single next action when you pick this up:** create the GitHub repo under your personal account and push, then work down the release checklist in section 1.
