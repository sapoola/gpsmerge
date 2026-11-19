# The two jobs gpsmerge does: stack and append

gpsmerge exists to combine activity files. There are exactly two ways to combine them, and they answer two different questions. Get this distinction clear and the rest of the tool makes sense.

- **Stack** answers: "I have two recordings of *the same ride at the same time*, each with sensors the other lacks. How do I get one file with everything?"
- **Append** answers: "I have two recordings of *different parts of one ride, one after the other*. How do I join them into a single continuous activity?"

Stack overlays in time. Append concatenates in time. That is the whole idea.

## Stack: two recordings of the same moment, different sensors

You went out for a ride. Your Garmin watch on your wrist recorded GPS, elevation, heart rate, and temperature. At the same time your smart trainer (or a separate power meter head unit) recorded power and cadence but had no GPS. Both files cover the same hour, second by second, but each is missing channels the other has.

Stacking takes one file as the base and borrows a channel from the other. You keep the GPS track from the watch and pull power in from the trainer, lining the two up by their timestamps. The result is one file: your real route, with power on top.

Concrete cyclist examples:

- **Watch + trainer.** GPS, route, and heart rate come from the watch; power and cadence come from the trainer. Common when the head unit that talks to your power meter doesn't do GPS, or you simply trust one device's GPS more.
- **Head unit + chest strap that logged separately.** Your Edge has the route; a separately recorded heart-rate log fills in the HR your Edge dropped.
- **Two head units, one with a better sensor.** Both rode the whole way; you want the cleaner power trace from one and the route from the other.

The two recordings must overlap in time. The donor's samples are on their own clock, so gpsmerge projects them onto the base file's timestamps by interpolating between the donor's nearest readings (see `resample.py`). Where the donor genuinely stopped recording (a real dropout, not just a different sample rate) no value is invented; that point simply has no borrowed channel.

The mental picture: two transparent sheets laid on top of each other, aligned by the clock. You look down through both and read off whichever sensor you want from each.

## Append: two recordings end to end, one continuous ride

Different problem. Your battery died forty kilometres into a hundred-kilometre ride. You pulled out your phone, started a new recording, and finished the day on that. Now you have two files: file A covers the first forty kilometres, file B covers the last sixty. They do **not** overlap: file B starts roughly where file A stopped.

Appending joins them head to tail into one activity, in time order, so it reads as the single ride it really was.

Concrete cyclist examples:

- **Battery died mid-ride.** Head unit recorded the first half; phone recorded the second half after you swapped.
- **Accidental stop and restart.** You hit stop at the café, started a fresh recording when you rolled out, and ended up with two files for one outing.
- **Device swap.** You changed head units partway (a mount broke, a reboot) and want the day stitched back together.

Because the two recordings are sequential, not simultaneous, appending is about ordering and about honouring the gap between them. The pause between "battery died" and "phone started" is a real break in the ride. gpsmerge preserves that break as a boundary between segments rather than pretending you teleported from one point to the next (see the note on segments in `data-model.md`).

The mental picture: two strips of film spliced end to end into one reel, in the order they happened.

## Telling them apart

If you find yourself asking "*which sensor* do I want from *which file*?", that is a **stack**: same time, different channels. If you are asking "*which file came first*?", that is an **append**: different times, joined in sequence.

Everything else gpsmerge does (reading files into one shared model, converting between formats, writing the result back out) is in service of these two jobs.
