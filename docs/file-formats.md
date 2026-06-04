# A gentle primer on GPX, TCX, and FIT

gpsmerge reads and writes activity files. There are three formats you will meet in the wild, and they each hold roughly the same kind of data — a list of timestamped points with position and sensor readings — but they store it very differently. This page explains each one, the quirks that trip people up, and which ones gpsmerge handles today.

## The short version

| Format | Shape | gpsmerge today |
| ------ | ----- | -------------- |
| GPX    | XML text | reads and writes |
| TCX    | XML text | reads and writes |
| FIT    | binary | reads (writing comes later) |

Today gpsmerge **reads and writes GPX and TCX, and reads FIT**. Writing FIT remains a later, experimental milestone; the data model already carries a `source_format` of `"gpx"`, `"tcx"`, or `"fit"`, so a FIT file you read merges and writes out as GPX or TCX just like any other input.

## GPX — the lingua franca

GPX (GPS Exchange Format) is plain XML text you can open in any editor and read. Almost every device, website, and tool can import or export it, which is exactly why gpsmerge speaks it first.

A GPX file is a tree: a track (`<trk>`) holds one or more track segments (`<trkseg>`), and each segment holds track points (`<trkpt>`). Every track point carries latitude and longitude as attributes, and usually elevation and a timestamp as child elements.

The quirk that surprises everyone: **core GPX has no field for heart rate, cadence, power, or temperature.** Those fitness channels live in *extensions* — extra XML tucked under a `<extensions>` element, in Garmin's own namespaces:

- `gpxtpx` (the Garmin **TrackPointExtension**) wraps heart rate (`hr`), cadence (`cad`), and temperature (`atemp`) inside one `TrackPointExtension` element.
- `gpxpx` (the Garmin **PowerExtension**) carries power as `PowerInWatts`, sitting alongside as a sibling.

Different writers use different namespace prefixes, or none at all, for the same data. gpsmerge copes by matching extension elements on their **local name** (the tag with the namespace stripped off), so a file is understood whether its author wrote `gpxtpx:hr`, some other prefix, or a bare `hr`. On the way out, gpsmerge writes the channels back under the standard Garmin namespaces so the result imports cleanly into Strava and Garmin Connect. See `gpx.py` for the details.

Two more GPX facts worth knowing:

- The top-level `creator` attribute names the device or app that produced the file (for example `"Garmin Edge 530"`). gpsmerge reads it into the activity's device and writes it back out.
- A channel that is missing is simply **left out** of the XML — never written as `0`. A missing heart rate is an absent element, not zero beats per minute.

## TCX — laps and structure

TCX (Training Center XML) is Garmin's older XML format. Like GPX it is human-readable text, but it is organised around **structure** rather than a bare list of points.

A TCX file is built from laps. An activity contains one or more `<Lap>` elements, and the actual trackpoints live *inside* a lap, under a `<Track>`. So where GPX gives you "here are the points", TCX gives you "here are the laps, and each lap contains its points". You cannot have a trackpoint that belongs to no lap — the lap structure is mandatory.

TCX stores the fitness channels more directly than GPX: heart rate, cadence, and distance have defined places in the core schema, so you do not have to hunt through namespaced extensions the way GPX makes you. Power and instantaneous speed live in Garmin's ActivityExtension (`TPX`) namespace — `Watts` and `Speed` — which gpsmerge reads and writes. There is one notable gap: **TCX has no temperature field.** If a ride's temperature matters to you, TCX will lose it.

TCX also records a `<Creator>` element naming the recording device, which is the TCX equivalent of GPX's `creator` attribute; gpsmerge reads and writes the device name, and the unit id and product id when present.

gpsmerge **reads and writes TCX**, including its laps. On read each `<Lap>` becomes a lap in the shared model — a boundaries-only overlay holding the lap's start, its end (the start plus `TotalTimeSeconds` when present), and its mapped trigger (`Manual` → `manual`, `Distance` → `distance`, `Time` → `time`, `Location` → `position`, `HeartRate` → `heart_rate`). Laps are kept independent of the segment (pause) structure: every `<Track>` (across all laps, in order) becomes a segment, so a pause between tracks survives as a segment boundary whether or not it falls on a lap boundary. A file may also hold several `<Activity>` elements — multisport and Training Center exports do this — and gpsmerge reads them all, concatenating every activity's tracks _and_ laps in document order (the sport, device, and creation time come from the first activity). Timestamps are written to whole seconds when a point lands on one and to millisecond precision when it carries a fractional second, so sub-second points (time is the merge key) are not collapsed.

On write, an activity that has laps emits one `<Lap>` per lap in start-time order; every point is assigned to a lap by start-time partition (a point belongs to the latest lap that started at or before it, and any point before the first lap joins it) so no point is ever lost, and within a lap the points still split into a `<Track>` at each segment boundary, so a pause inside a lap survives as separate tracks. Each lap's `TotalTimeSeconds`, `DistanceMeters`, and `TriggerMethod` are recomputed from the points that fall in the lap rather than trusting any stored value, so they never go stale after a merge. An activity with no laps — for example one read from GPX, which has no lap concept — writes a single synthetic `<Lap>` spanning the whole activity with one `<Track>` per segment, exactly as before.

Two losses are worth calling out. The first is temperature: because TCX has no field for it, `temp` is dropped on write and reads back as `None`, exactly how the model represents any absent channel. The second is the **per-lap summary detail**: only a lap's span and trigger round-trip. Its `<Calories>` and `<Intensity>` are written as fixed placeholders and are not retained on read, and the recomputed totals replace whatever the source recorded.

## FIT — compact, binary, and everywhere on real devices

FIT (Flexible and Interoperable Data Transfer) is the format your Garmin, Wahoo, or other head unit actually records to. Unlike GPX and TCX it is **binary**: you cannot open it in a text editor and read it. It is a stream of typed "messages" defined by a profile, which makes it compact and fast for a device to write, but means you need a real parser to make any sense of it.

A few quirks that matter:

- **Positions are stored as "semicircles", not degrees.** FIT records latitude and longitude as signed 32-bit integers where a full circle is `2**32` units, so `2**31` semicircles is 180 degrees. gpsmerge converts them to decimal degrees on read with `semicircles_to_degrees` from `geo.py`. The conversion is lossless to within roughly `1e-5` degrees, the quantisation step of a single semicircle.
- **Timestamps count from a different epoch.** FIT seconds are measured from 1989-12-31T00:00:00Z, not the Unix epoch. The `fitdecode` parser decodes them to datetimes for us, and `read_fit` normalises every one to the model's timezone-aware UTC.
- **Some channels come in two flavours.** FIT carries both `altitude`/`speed` and wider-range `enhanced_altitude`/`enhanced_speed`. gpsmerge prefers the enhanced field when it is present, because it is the one a modern device records when the plain field would clip, and falls back to the plain field otherwise.
- It is the richest of the three formats — device identity (manufacturer, product, serial number, software version) and many sensor channels are all available — which is why the data model's `Device` has structured fields that mainly come from FIT.

gpsmerge **reads FIT** via the `fitdecode` parser (see `fit.py`). Each `record` message becomes a track point, the `file_id` message supplies the device identity and creation time, and the `sport` (or `session`) message supplies the sport. Recording pauses survive as segment boundaries: a `timer` `stop` event closes the current segment and the next `timer` `start` opens a fresh one, exactly as a pause between TCX tracks does. **Writing FIT remains a later, experimental milestone** — for now a FIT file you read is written back out as GPX or TCX.

## Why this matters for merging

Because the three formats disagree on layout — extensions vs. structured fields, text vs. binary, degrees vs. semicircles, which channels even exist — gpsmerge does not merge files in their native shapes. Every reader converts its format into one shared model first (see `data-model.md`), the merge happens on that model, and a writer turns the result back into a file. That is why a quirk like "TCX has no temperature" or "FIT uses semicircles" stays contained in one reader and never leaks into the merge logic.
