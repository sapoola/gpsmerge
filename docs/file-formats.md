# A gentle primer on GPX, TCX, and FIT

gpsmerge reads and writes activity files. There are three formats you will meet in the wild, and they each hold roughly the same kind of data — a list of timestamped points with position and sensor readings — but they store it very differently. This page explains each one, the quirks that trip people up, and which ones gpsmerge handles today.

## The short version

| Format | Shape | gpsmerge today |
| ------ | ----- | -------------- |
| GPX    | XML text | reads and writes |
| TCX    | XML text | reads and writes |
| FIT    | binary | reading comes later |

Today gpsmerge **reads and writes GPX and TCX**. FIT reading is planned for a later milestone; the data model already has room for it (it carries a `source_format` of `"gpx"`, `"tcx"`, or `"fit"`), but FIT is not wired up yet.

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

gpsmerge **reads and writes TCX**. On read the mandatory lap structure is flattened into the shared model: every `<Track>` (across all laps, in order) becomes a segment, so a pause between tracks survives as a segment boundary. On write gpsmerge emits a single lap — it does not model laps yet — containing one `<Track>` per segment, so those pauses are preserved. The one documented loss is temperature: because TCX has no field for it, `temp` is dropped on write and reads back as `None`, exactly how the model represents any absent channel.

## FIT — compact, binary, and everywhere on real devices

FIT (Flexible and Interoperable Data Transfer) is the format your Garmin, Wahoo, or other head unit actually records to. Unlike GPX and TCX it is **binary**: you cannot open it in a text editor and read it. It is a stream of typed "messages" defined by a profile, which makes it compact and fast for a device to write, but means you need a real parser to make any sense of it.

A few quirks that matter:

- **Positions are stored as "semicircles", not degrees.** FIT records latitude and longitude as signed 32-bit integers where a full circle is `2**32` units, so `2**31` semicircles is 180 degrees. They must be converted to decimal degrees before they mean anything to the rest of the tool. gpsmerge already has the conversion helpers ready in `geo.py` (`semicircles_to_degrees` and back) for when FIT reading lands.
- **Timestamps count from a different epoch.** FIT seconds are measured from 1989-12-31T00:00:00Z, not the Unix epoch. `timeutil.py` carries `fit_timestamp_to_utc` to translate them into the model's UTC datetimes.
- It is the richest of the three formats — device identity (manufacturer, product, serial number, software version) and many sensor channels are all available — which is why the data model's `Device` has structured fields that mainly come from FIT.

gpsmerge does not read FIT yet either; it is a later milestone. The geometry and time helpers it will need already exist and are tested, so the remaining work is the binary parsing itself.

## Why this matters for merging

Because the three formats disagree on layout — extensions vs. structured fields, text vs. binary, degrees vs. semicircles, which channels even exist — gpsmerge does not merge files in their native shapes. Every reader converts its format into one shared model first (see `data-model.md`), the merge happens on that model, and a writer turns the result back into a file. That is why a quirk like "TCX has no temperature" or "FIT uses semicircles" stays contained in one reader and never leaks into the merge logic.
