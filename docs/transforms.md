# Post-merge transforms

After gpsmerge combines your files into one activity, it can adjust that activity before writing it. These transforms are optional and off by default; each is a single flag on `gpsmerge run`. They never touch the input files, only the merged result, and the written timestamps stay UTC.

When more than one is requested they are applied in a fixed order: trim, then shift, then split long gaps into pauses, then smooth elevation. The order means a trim shrinks the track first, and the re-based distance and re-segmenting all see a clean, trimmed track.

## Shift the clock

`--shift <duration>` adds a signed duration to every timestamp. Use it when a device recorded with a wrong clock so the whole track is off by a constant amount. The duration is `+1h`, `-30m`, `90s`, a decimal like `45.5s`, or a clock form like `+1:30:00` (H:MM:SS) or `-0:02:30` (MM:SS). A leading `+` is optional; `-` moves the clock backwards.

```
gpsmerge run a.gpx b.gpx --out ride.gpx --shift +1h
```

## Trim the ends

`--trim-start` and `--trim-end` keep only the points inside an ISO time window; either bound can be left off to leave that side open. Use it to cut a warm-up or a cool-down. After trimming, cumulative distance is re-based to start at zero again, so the trimmed track reads "0 km at the start" rather than inheriting the distance of the points that were cut.

```
gpsmerge run a.gpx b.gpx --out ride.gpx --trim-start 2026-06-04T08:30:00Z --trim-end 2026-06-04T09:30:00Z
```

## Smooth elevation

`--smooth-elevation <window>` replaces each point's elevation with a centred moving average over its neighbours, which tames the spikes a noisy barometric altimeter produces. The window is the full width and must be an odd integer of at least 3 (so it is centred and actually averages). Points with no recorded elevation stay blank, and only elevation is changed.

```
gpsmerge run a.gpx b.gpx --out ride.gpx --smooth-elevation 5
```

## Count a long gap as stopped time

`--insert-stopped-time` splits the track wherever two consecutive points are more than the threshold apart in time, turning that gap into a pause. Moving time is measured within segments, so once a long non-moving gap is a segment boundary it no longer counts as moving time. Set the threshold with `--stopped-time-threshold` (seconds, default 30).

```
gpsmerge run a.gpx b.gpx --out ride.gpx --insert-stopped-time --stopped-time-threshold 60
```
