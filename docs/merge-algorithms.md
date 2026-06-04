# How the merge algorithms work

This page is the close-up of the merges gpsmerge does: **append** (join recordings that happened one after another into a single continuous activity) and **stack** (overlay two recordings of the same ride, taking a different sensor from each). It also covers **auto mode**, which guesses which of the two a set of files wants. If you have not yet read `concepts.md`, start there — it draws the line between *append* (concatenate in time) and *stack* (overlay in time). The append walkthrough comes first, then stack, then auto.

The code lives in `src/gpsmerge/merge.py`, in the `append`, `stack`, and `detect_mode` functions. Everything below describes what those functions actually do, step by step.

## What appending is, and when you reach for it

Appending splices two or more recordings end to end, in the order they happened, so a ride that got split across files reads as the single ride it really was.

You use it when one outing ended up in more than one file:

- Your bike computer's battery died forty kilometres in, you pulled out your phone, and you finished the day on that. File A has the first forty kilometres; file B has the rest.
- You hit stop at the café, started a fresh recording when you rolled out, and went home with two files for one ride.
- A mount broke or a device rebooted, you swapped head units partway, and you want the day stitched back together.

The defining feature is that the recordings are **sequential, not simultaneous**. File B starts roughly where file A stopped; they do not cover the same wall-clock time. (Two recordings of the *same* time, each with sensors the other lacks, are a *stack* — see the closing note.)

## A worked example

Two short recordings of one ride. Times are UTC, positions are made up but consistent, and a `·` marks one recorded point.

File A — head unit, then the battery died:

```
12:00:00  (52.0000, 4.0000)  ·
12:00:01  (52.0001, 4.0000)  ·
12:00:02  (52.0002, 4.0000)  ·   <- last point before the battery died
```

File B — phone, started after you swapped:

```
12:02:30  (52.0002, 4.0000)  ·   <- first point on the phone, same spot as A's last
12:02:31  (52.0003, 4.0000)  ·
12:02:32  (52.0004, 4.0000)  ·
```

There is a gap of about two and a half minutes between A's last point (`12:00:02`) and B's first (`12:02:30`) — the time it took to notice, stop, swap, and restart. Appending these two files has to decide three things: what order they go in, what to do with that gap, and whether the point at `12:02:30` is really a new point or just A's last point recorded twice. The rest of this page is those three decisions.

## Ordering by start time

The first thing `append` does is sort the inputs by **start time** — the earliest timestamp in each recording — not by the order you happened to pass them on the command line. File A starts at `12:00:00` and file B at `12:02:30`, so A comes first regardless of how you listed them.

A recording with no timestamps at all (a bare course or route with no clock) sorts **last**, so it never wedges itself into the middle of timed recordings. There is no special branch for this case; the sort just hands those activities a far-future key.

## Segments and the join

A **segment** is one continuous run of recording. A device starts a new segment wherever it paused — a stop light, a coffee stop, switching off and on. Segments are what let a writer tell *moving* time from *elapsed* time, so they matter and append takes care to preserve them. (See `data-model.md` for the model itself.)

Two rules govern segments during an append:

1. **Each input keeps its own segment boundaries.** If file A was internally split into two segments because you waited at a junction, those two segments survive into the output unchanged.
2. **The join between two inputs is itself a new segment boundary.** A's points and B's points never land in the same segment. The merged activity ends one segment at A's last point and begins a fresh one at B's first.

Rule 2 is what stops the merged file from drawing a straight line across the gap. The break between "battery died" and "phone started" was a real pause in the ride; keeping it as a segment boundary means tools downstream see a pause there, not a 2.5-minute, several-hundred-metre teleport.

In the worked example the result has two segments: `{12:00:00, 12:00:01, 12:00:02}` from A, then `{12:02:30, 12:02:31, 12:02:32}` from B.

## Gap policy: pause versus shift

The gap between A's last point and B's first is a real span of time. You choose what to do with it through `gap_policy`, set on the command line with `--gap-policy`.

### `pause` (the default)

Keep every timestamp exactly as recorded. The 2.5-minute gap stays in the file, and downstream tools show it as stopped time. This is the honest default: the clock on the merged activity matches the clock on the day, and your moving-versus-elapsed split is real.

With `pause`, the worked example's timestamps are untouched — A's run ends at `12:00:02`, B's begins at `12:02:30`, and the two and a half minutes in between are simply absent from any segment, which is exactly how a real stop looks.

### `shift`

Move the later recording earlier in time so it starts **right after** the earlier one, as if you had never stopped. Use this when the gap is an artefact you do not want — the time spent fumbling with the phone is not part of the ride you care about — and you would rather the two recordings butt straight up against each other.

The important detail is *how far* to shift. gpsmerge does not assume a hard-coded one second between the recordings. It places B's first point one **sample** after A's last, where a sample is B's own local spacing, measured from B's first two timestamps. If B was recording every second, B is shifted to start one second after A ends; if B was on a five-second smart-recording interval, B starts five seconds after A ends. Only when B has fewer than two timestamps to measure from (so its spacing cannot be read) does append fall back to a nominal one second.

In the worked example, B's first two points are one second apart (`12:02:30` then `12:02:31`), so under `shift` B is moved to start one second after A's `12:00:02` — at `12:00:03` — and the whole of B slides earlier by the same amount, keeping B's internal spacing intact.

Two things `shift` does **not** do: it never changes the order of the inputs (that was fixed by start time), and it never touches the first recording (there is nothing before it to follow).

## Seam dedupe: dropping a doubled point

Some devices write the same point twice at a boundary — the last record of one recording and the first record of the next describe the same instant in the same place. In the worked example, A's last point and B's first are both `(52.0002, 4.0000)` at essentially the same moment. Left alone, that doubled point is a small blemish: a zero-distance, zero-time step right at the join.

When `dedupe` is on (the default), append drops the **first point of a segment** when it duplicates the **last point of the segment before it**. Two points count as a duplicate only when they are close in *both* time and space:

- within `SEAM_MAX_TIME_S` = **1 second** of each other, and
- within `SEAM_MAX_DISTANCE_M` = **5 metres** of each other.

Both thresholds have to pass. This is deliberately tight, and the reason is worth understanding: a genuine *next* point is always at least one sample apart in time — a second, five seconds, whatever the device records at — so a real pair of consecutive points cannot both be sub-second-apart and sub-5-metres-apart. Only a record that was literally written twice clears both bars. That is why dedupe catches doubled boundary records without ever eating a real point: a true pause within the ride is far enough apart in time not to match, and two distinct points on the move are far enough apart in space.

A point with no timestamp, or with no position, is never treated as a duplicate — append cannot prove it is one, so it leaves it alone.

## Distance: recomputed and continuous

When `recompute_distance` is on (the default), append throws away whatever cumulative distance the source files carried and recomputes it from scratch, from the positions, after the points have been ordered, shifted, and de-duplicated. It walks the merged points in order and adds the straight-line (haversine) distance between each consecutive pair, writing a running cumulative total onto every point.

Two properties matter here:

- **It runs continuously across segment boundaries.** A pause does not reset the total — the distance you had already covered carries straight over the join into the next segment. The merged activity's distance is the distance of the whole ride, not of its last piece.
- **Distance is never interpolated and never goes backwards.** Each point's distance is the previous point's distance plus a real, non-negative step between two real positions. There is no smoothing and no invented mileage; the number only ever climbs.

### Keeping a gap from inflating the distance

There is a wrinkle at the seam. Between A's last position and B's first there can be a real jump on the map — you wheeled the bike into a café and back out, or the GPS glitched and snapped your position a few hundred metres away. Counting that jump as distance ridden would silently inflate your total.

`--ignore-gaps-over-meters` (the `ignore_big_gaps_m` setting) guards against this. If you set it, any single step longer than that many metres is **not added** to the running total. The measurement still *resumes* from the new position — the points after the gap are measured against each other normally — but the one oversized hop across the gap contributes nothing. So a coffee-stop shuffle or a GPS spike at the seam cannot pad your distance, while the real riding on either side is counted in full. Leave the option unset and every step counts, however large.

## Device attribution

A merged file is recorded under **one** device's identity, even though it was stitched from several. By default that is the **first input** file's device; pass `--device-from <file>` (the `device_from` setting) to choose a different input's device instead. So a ride that started on a Garmin Edge and finished on a phone can be attributed to whichever of the two you want to call the recorder.

The chosen device is *copied* into the merged activity rather than shared, so the output never holds a reference back into one of the inputs.

## The correctness rules append relies on

Append leans on a handful of invariants. They are quiet when everything works, but they are the reason the output is trustworthy.

- **Absent is `None`, never `0`.** A missing channel — no heart rate, no power, no position — is `None` throughout the model. Append never reads a missing value as a zero, which is what keeps a device with no GPS from contributing `(0, 0)` off the coast of Africa to your distance.
- **Every timestamp is timezone-aware UTC.** Ordering by start time, measuring the gap, and shifting all do arithmetic on timestamps; because they are all UTC, that arithmetic is unambiguous and never trips over a timezone or a daylight-saving change.
- **Distance is never interpolated and is monotonic.** As above: it is summed from real positions, skips oversized gaps when you ask it to, and only ever increases.
- **Merge never mutates its inputs.** Every point that goes into the output is a *copy*. Append copies a point even when it is not shifting its time, precisely so that recomputing distance writes onto the copies and never onto the activities you read in. You can append the same loaded activities twice and get the same answer both times.

# How stacking works

This is the other half of the module: `stack` in `src/gpsmerge/merge.py`. Where append joins recordings that happened *one after another*, stack overlays recordings that happened *at the same time* — two files covering the same ride, each carrying a sensor the other lacks. The classic case is a watch that recorded GPS and heart rate plus a smart trainer (or power-meter head unit) that recorded power and cadence but had no GPS. Stacking gives you one file with your real route *and* the power on top.

## The master idea

Stacking starts by choosing one input as the **master**. The master is the spine of the output: it provides the **timeline, the position, and the elevation**, and those are copied through **verbatim**. The master's time, latitude, longitude, and elevation land in the output exactly as they were read — never interpolated, never averaged, never reconstructed. Position in particular is never invented. This is the single most important property of stacking, and it follows directly from a fact about sensor files: a device with no GPS does not leave its position blank, it often writes `(0, 0)` or stale garbage. If stacking ever let a donor supply position, that garbage could land on your map off the coast of Africa. So position only ever comes from the one file that actually recorded it, copied as-is.

By default the master is the input with the **most positioned points** — the file with the best GPS track, measured simply by counting how many of its points carry a latitude and longitude. In the watch + trainer case the watch wins automatically because the trainer has no positions to count. You can override the choice with `--position-from <file>` (the `position_from` setting) when you want a specific file to be the spine regardless.

## Borrowing the sensor channels

Everything that is *not* time, position, or elevation is a **sensor channel** that stacking can borrow: heart rate, cadence, power, temperature, and speed. Each of these is taken from its own chosen source and **resampled onto the master's timestamps**.

Resampling is the heart of it. The donor recorded its values on its own clock, which does not tick at the same instants as the master's. So for each timestamp in the master, stacking looks at the donor's two nearest readings — one just before, one just after — and **linearly interpolates** between them to get the value at exactly the master's instant. (The interpolation itself lives in `resample.py`; stack just feeds it the master's timestamps and the donor's samples.) The output therefore carries one heart-rate value, one power value, and so on at every master timestamp, all lined up on the master's clock.

### `--max-gap-seconds`: blank rather than guess

Interpolation between two close readings is trustworthy. Interpolation across a *long* hole in the donor's data is a guess, and stacking refuses to guess. `--max-gap-seconds` (the `max_gap_s` setting, default `10.0`) is the line. If the donor's two nearest readings straddling a master timestamp are more than that many seconds apart — the donor genuinely stopped recording the channel there, a real dropout rather than just a slower sample rate — the channel is left **blank** (`None`) at that timestamp rather than drawing a straight line across the hole. Short gaps are filled; long gaps are honestly left empty. The master's own point still exists with its position and time; it simply has no borrowed value for that channel there.

### Multi-donor fallback

A channel does not have to come from a single file. By default the **owners** of a channel are *every input that recorded it*, taken in input order. Stacking tries them in turn: the first owner supplies the value wherever it has one, and where the first owner has a gap (a `None` after the `max_gap_s` rule), the next owner is asked to fill it. So if two files both logged heart rate, the second can patch the holes the first left, and you get a more complete trace than either file alone.

Naming a source with `--hr-from` / `--cadence-from` / `--power-from` / `--temp-from` overrides that fallback: it forces the channel to come from **exactly that one file** and no other, even if other files also recorded it. Use the per-channel flag when you trust one device's reading and want it verbatim; leave it off to let a second file backfill gaps. (Speed has no flag of its own; it always uses the default every-owner-in-order behaviour.)

### Integer channels are rounded

Heart rate, cadence, and power are whole-number channels — you do not have a heart rate of 146.3. Interpolation produces a fractional value, so after resampling, these three are **rounded back to the nearest integer**. Temperature and speed keep their fractional precision.

## Distance and device on a stacked file

**Distance is recomputed from the master's positions**, exactly the way append recomputes it: walk the output points in order, add the straight-line (haversine) step between each consecutive pair, write the running cumulative total onto each point, and (if `--ignore-gaps-over-meters` is set) skip any single oversized step so a glitch cannot inflate the total. Because the positions are the master's verbatim track, the distance is the distance of that real track — never interpolated, never reconstructed from a donor.

**Device attribution defaults to the master.** A stacked file is recorded under one device's identity; by default that is the master (the position source), since the route is what the file is really *of*. Pass `--device-from <file>` (the `device_from` setting) to attribute it to a different input instead. As with append, the device is *copied* into the output, never shared with an input.

## A worked example: watch + trainer

Two recordings of the same five seconds of one ride. The watch has GPS and heart rate; the trainer has power but no GPS (its position is blank). A `·` marks a recorded value.

Watch (the master — it has the positions):

```
12:00:00  (52.0000, 4.0000)  hr 140  ·
12:00:01  (52.0001, 4.0000)  hr 142  ·
12:00:02  (52.0002, 4.0000)  hr 144  ·
12:00:03  (52.0003, 4.0000)  hr 146  ·
12:00:04  (52.0004, 4.0000)  hr 148  ·
```

Trainer (the donor — power only, on its own clock, no position):

```
12:00:00  power 200  ·
12:00:02  power 210  ·
12:00:04  power 220  ·
```

The watch is chosen as master automatically: it has five positioned points and the trainer has none. The output keeps the watch's time, position, elevation, and heart rate verbatim. Power is borrowed from the trainer and resampled onto the watch's one-second timestamps:

```
12:00:00  (52.0000, 4.0000)  hr 140  power 200   <- trainer sample, taken as-is
12:00:01  (52.0001, 4.0000)  hr 142  power 205   <- interpolated between 200 and 210
12:00:02  (52.0002, 4.0000)  hr 144  power 210   <- trainer sample, taken as-is
12:00:03  (52.0003, 4.0000)  hr 146  power 215   <- interpolated between 210 and 220
12:00:04  (52.0004, 4.0000)  hr 148  power 220   <- trainer sample, taken as-is
```

At `12:00:01` the trainer had no reading, so its 200-at-`12:00:00` and 210-at-`12:00:02` are interpolated to 205 — and because power is an integer channel, the result is rounded to a whole number. The trainer's gaps here are two seconds, well under the ten-second default, so they are filled. Had the trainer dropped power for, say, thirty seconds, those master timestamps would carry `power = None` rather than a guessed line across the hole. Distance is recomputed from the watch's positions, and the file is attributed to the watch.

## The correctness rules stacking relies on

Stacking leans on the same quiet invariants append does, plus one it cares about more than any other.

- **Position is never interpolated and never invented.** Time, latitude, longitude, and elevation come from the master verbatim. A donor never supplies position, so a sensor file's `(0, 0)` or stale coordinates can never reach your map.
- **Absent is `None`, never `0`.** A missing sensor value — a long donor dropout, a channel no file recorded — is `None` throughout, never a zero. A blank power reading is genuinely blank, not a misleading 0 watts.
- **Distance is never interpolated and is monotonic.** It is summed from the master's real positions, skips oversized gaps when you ask it to, and only ever increases.
- **Merge never mutates its inputs.** Every output point is a *copy* of the master's point with borrowed channels set onto the copy; the donor activities are only ever read. You can stack the same loaded activities twice and get the same answer both times.

# Auto mode: choosing stack vs append

You do not have to tell gpsmerge which merge you want. With `--mode auto` (the default) it inspects the files and decides for itself, in `detect_mode`. The decision is a deliberately simple, **time-based heuristic**: it asks how much the recordings overlap on the wall clock.

For each input it takes the span from its first timestamp to its last. It then measures the **overlap** — the stretch of time every recording was running at once (the latest start to the earliest end) — and expresses that overlap as a **fraction of the shortest recording's span**. Using the shortest span as the yardstick is what makes a small file fully contained inside a big one read as a near-total overlap, which is exactly the stack case (a short trainer file sitting inside a longer watch ride).

The fraction then picks the mode:

- **Above ~0.8** (the `STACK_OVERLAP_RATIO`): the recordings cover essentially the same time, so they are two takes of one ride → **stack**.
- **Below ~0.1** (the `APPEND_OVERLAP_RATIO`): the recordings barely touch in time, so they are sequential pieces → **append**.
- **In between** (roughly 0.1 to 0.8): gpsmerge will not guess. The result is **ambiguous**, and you must say which you meant with `--mode stack` or `--mode append`.

A few inputs are ambiguous up front, before any ratio is computed: fewer than two recordings, any recording with no timestamps at all (no clock to compare), or a shortest span of zero. In all of these `detect_mode` returns `ambiguous` and asks you to choose.

This heuristic looks only at the clock, not at the map. It does not check whether the overlapping recordings actually traced the *same route* — two genuinely different rides that happened to run at the same time of day would read as a stack on time alone. In practice that is rare, and the simple time test is enough; a position cross-check (confirming the overlapping tracks really sit on top of each other) is a natural future refinement, not something auto mode does today.

If you are deciding by hand instead, `concepts.md` has the short version: ask "which file came first?" for append, "which sensor from which file?" for stack.
