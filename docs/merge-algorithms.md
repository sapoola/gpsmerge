# How append works

This page explains the one merge algorithm gpsmerge ships today: **append**. It joins recordings that happened one after another into a single continuous activity. If you have not yet read `concepts.md`, start there — it draws the line between *append* (concatenate in time) and *stack* (overlay in time). This page is the close-up of append.

The code lives in `src/gpsmerge/merge.py`, in the `append` function. Everything below describes what that function actually does, step by step.

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

## A note on stack

The other way to combine files — **stack**, overlaying two recordings of the *same* time so you can take, say, the route from one and power from the other — is a separate, later milestone. It will live alongside `append` in this same module. Everything on this page is about append only; if you are trying to decide which one you want, `concepts.md` has the short version: ask "which file came first?" for append, "which sensor from which file?" for stack.
