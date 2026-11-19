# Workout detection (BETA)

`--detect-workout` looks at a merged activity, recovers the structured interval workout hidden in its effort trace, and writes that structure back as laps plus a short summary line. It is a best-effort heuristic, shipped as **BETA**: it shines on structured indoor and interval sessions and is honest about where it struggles. Treat the laps it produces as a good first guess, not gospel, and glance at them before relying on them.

It needs the optional `workout` extra (numpy): `pip install 'gpsmerge[workout]'` (or `uv sync --extra workout`). The core install stays numpy-free; the extra is only pulled in when you actually detect a workout.

## What it does

A trainer session or a track workout is a sequence of held efforts: "5 minutes at 300 W, 5 minutes at 180 W, 30 seconds at 500 W". The recording stores every sample but no structure: the steps are implicit in the trace. The detector finds those steps and expresses them two ways:

- **Laps.** One lap per detected step, written through the same machinery as `--auto-lap`, so the structure persists into TCX and FIT (where laps round-trip). GPX has no laps and drops them.
- **A summary line.** A one-line, human-readable description shown in the preview and carried in `--json`, e.g. `5:00 @ 300 W, 5:00 @ 180 W, 0:30 @ 500 W` for a ride, or `3:00 @ 4:15/km, 1:00 @ 5:30/km` for a run.

## Power for rides, speed for runs, chosen automatically

There is no flag to pick the channel. The detector chooses it for you:

- **Power** when the activity has a varying power channel (a ride).
- **Speed** when it has a varying speed channel instead (a run, paced by speed).
- Otherwise it stops with a clear "no usable power or speed data to detect a workout from" error.

A ride summary is rendered in watts (rounded to the nearest 5 W). A run summary is rendered in pace per kilometre (`1000 / speed` seconds per km, as `mm:ss/km`), because that is how runners read intervals.

## The significance threshold scales to the ride's own average

The one idea worth understanding is how a real step is told from noise. A boundary between two steps counts only when the level shifts by at least

```
delta_min = max(FLOOR, K_AVG × ride_average)
```

where `ride_average` is the plain mean of the chosen channel's recorded samples: one cheap `mean()`, no FTP, no percentile, no user input. `K_AVG` is 0.15 (a real interval is a sustained change of at least 15% of the ride's own average effort), and the per-channel floors are 20 W for power and 0.3 m/s for speed.

**Why scale to the ride's own average?** Because the same absolute change means different things to different riders. Consider a 100 → 130 W step:

- A rider whose ride averages ~110 W sees +30 W as +27% of their average, a genuine interval. The detector splits it into two efforts.
- A rider whose ride averages ~350 W sees the same +30 W as ~8.5% of their average, noise within one block. The detector keeps it as one effort.

That self-calibration is the whole point: it works for an easy recovery spin and a savage threshold session without you tuning anything. The same logic applies to speed, scaled to the average speed.

## The min-seconds knob

`--workout-min-seconds` (default 10) is the shortest interval the detector will report as its own lap: any segment shorter than this is folded into a neighbour. So at the default a 20-second 500 W sprint is kept while a 4-second blip is merged away as noise. This is the one lever for the granularity of the overlay: raise it for a coarser detection (fewer, longer laps; on a busy interval ride, raising it to 60 folds the short reps into their surrounding blocks), lower it to keep briefer efforts.

## Where it shines, and where it struggles

**Shines:**

- Structured trainer and interval workouts (ERG-mode sessions, threshold repeats, VO2 sets).
- Anything with clear, held steps and clean transitions between them.

**Struggles:**

- **Smooth ramps.** A continuous build from 100 to 400 W has no real steps; the detector approximates it with a handful of coarse blocks rather than the smooth curve it is. It will not explode a ramp into dozens of tiny laps, but it cannot invent structure that is not there.
- **Unstructured outdoor riding.** A hilly road ride with constantly varying power has no clean interval structure, so the laps will be a rough sketch at best.
- **Long recording dropouts.** If the effort channel goes missing for minutes (a disconnected sensor), the gap is bridged rather than broken at, so it folds into the neighbouring interval: that interval's duration then includes the dropout, even though its reported average still reflects only the real samples. Detection assumes a continuously recorded effort.

In both cases the output is a best-effort approximation. That is why this is BETA, and why choosing `--detect-workout` prints a one-line heads-up on stderr.

## Notes

- `--detect-workout` and `--auto-lap` both set the laps, so they are mutually exclusive: pass one or the other, not both. The detector runs last in the transform pipeline, after any trim, shift, and re-time, so it sees the final data.
- The lap trigger is recorded as `power` or `speed` internally; TCX and FIT both degrade it to their safe `manual` default on write, which is exactly right for a detected (rather than device-recorded) lap.
