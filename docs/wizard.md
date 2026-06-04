# The guided wizard

The wizard is the easiest way to merge files with gpsmerge. You do not need to know any flags or modes -- it asks plain questions, shows you what it found, and only writes a file once you say yes. Run it by typing the bare command in a terminal:

```
gpsmerge
```

If you pipe input or run in CI (no terminal attached), gpsmerge does not start the wizard -- it prints a short hint and exits, so an automated run never hangs waiting for an answer. For scripts, use `gpsmerge run` with flags instead (see `gpsmerge examples`).

## A walkthrough

Say your bike computer died halfway through a ride and you finished the recording on your phone. You now have two files, `morning.gpx` and `evening.gpx`, and you want one clean activity. Here is the full session.

### 1. What do you want to do?

```
? What do you want to do?
 » Append -- my ride was split across two recordings
   Stack -- two devices recorded the SAME ride
   Not sure -- look at my files
```

Pick **Append**. If you are not sure, choose the last option and gpsmerge works it out from the timestamps.

- **Append** joins recordings that happened one after another into one continuous activity.
- **Stack** overlays two recordings of the *same* ride, taking a different sensor from each (for example GPS from your watch and power from a trainer).

### 2. Add your files

```
? Add the first file:  morning.gpx
Files so far:
  1. morning.gpx
? Add another file (1 so far), or press Enter to finish:  evening.gpx
Files so far:
  1. morning.gpx
  2. evening.gpx
? Add another file (2 so far), or press Enter to finish:
```

Type a path (Tab completes file names) and press Enter. The running list grows as you go. Press Enter on a blank line to finish. You need at least two files; the wizard keeps asking until you have them. A file that does not exist, or that is not a `.gpx` or `.tcx`, is rejected with a short message and you are asked again.

### 3. What gpsmerge detected

```
          What I see
┏━━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━┓
┃ File        ┃ From     ┃ To       ┃
┡━━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━┩
│ morning.gpx │ 08:00:00 │ 08:42:10 │
│ evening.gpx │ 09:05:00 │ 09:48:30 │
└─────────────┴──────────┴──────────┘
Looks like: append
```

gpsmerge shows each file's time span and what it thinks you want. Because the two recordings do not overlap, it agrees this is an append and moves on. If your choice had *contradicted* the data -- say you picked Stack but the files are clearly sequential -- the wizard would not crash. It offers you the recommended switch:

```
? You chose stack, but the files look like an append. Which should I use?
 » Use append (recommended -- matches the data)
   Keep stack (I know what I'm doing)
```

### 4. Append options

```
? There may be a gap between the recordings. How should I treat it?
 » Pause -- keep the real times (gap shows as stopped)
   Shift -- move the later ride to start right after the first
? Keep big position jumps out of the distance total?  (y/N)
```

**Pause** keeps the real timestamps, so the coffee-stop gap shows as stopped time. **Shift** slides the second recording so it starts right after the first, with no gap. Answering yes to the second question stops a large jump between the two recordings from inflating your distance.

> If you had chosen **Stack** instead, this step is replaced by a channel-coverage table and questions about which file provides position, heart rate, power, and so on. A channel is only asked about when more than one file has it; if just one file has GPS, the wizard tells you that and does not ask.

### 5. Which device?

```
? Which device should the merged file be recorded under?
 » Garmin Edge 530  (morning.gpx)
   Apple Watch  (evening.gpx)
```

The merged file is attributed to one device's identity. Pick whichever you want the activity recorded under.

### 6. Format, sport, and clean-up

```
? Output format?
 » GPX (works everywhere)
   TCX (no temperature channel)
? Sport for the merged file? (leave blank to keep the source's)
? Apply standard clean-up (drop seam duplicates, recompute distance)?  (Y/n)
```

GPX is the safe default and is read by every tool. TCX is also supported, but it cannot carry a temperature channel. Leave the sport blank to keep whatever the source files say. The clean-up step removes the duplicated point some devices write where two recordings meet, and recomputes the distance from the positions -- leave it on unless you have a reason not to.

### 7. Where to write

```
? Where should I write the merged file?  /home/you/rides/merged.gpx
```

The default is `merged.<format>` next to your input files. If the file already exists, the wizard does not silently overwrite it:

```
? merged.gpx already exists. What now?
 » Overwrite it
   Pick a different name
   Cancel
```

### 8. Preview, then write

```
╭───────── Preview ─────────╮
│    points  2484           │
│    moving  1h 25m 40s     │
│            (elapsed 1h 48m 30s) │
│  distance  41.20 km       │
│  channels  position, elevation, hr, power │
│    device  Garmin Edge 530 │
╰───────────────────────────╯
? Write it?  (Y/n)
```

Nothing is written until you confirm. Review the points, time, distance, channels, device, and any warnings. Answer **yes** to write the file; answer **no** and gpsmerge exits cleanly with nothing written.

## Stopping early

Press **Ctrl-C** at any prompt to stop. The wizard exits without writing a file and tells you so -- you never see a stack trace.

## When something is wrong

If the merge cannot proceed -- for example a file has no timestamps, or the chosen output directory is not writable -- the wizard prints the problem in plain language and exits with the same status code the `gpsmerge run` command would use, so the behaviour is consistent however you run the tool.
