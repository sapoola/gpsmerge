# Saved profiles

A *profile* is a saved set of the general merge settings: the ones that do not depend on which files you happen to be merging this time. If you merge the same kind of files the same way every week, a profile lets you save those choices once and reuse them with a single flag.

## The problem profiles solve

Say that every week you merge a trainer FIT file and a watch TCX file the same way: stack them, write TCX, and show times in Amsterdam. Without a profile you retype the same flags every time:

```
gpsmerge run trainer.fit watch.tcx --out ride.tcx \
  --mode stack --format tcx --timezone Europe/Amsterdam
```

A profile turns those repeated flags into one name.

## Save a profile

Add `--save-profile <name>` to a normal `run`. The merge happens as usual, and the general settings are saved under that name afterwards:

```
gpsmerge run trainer.fit watch.tcx --out ride.tcx \
  --mode stack --format tcx --timezone Europe/Amsterdam \
  --save-profile trainer
```

You can also save from the guided wizard: at the very end, after it writes the file, it asks "Save these settings as a profile?". Type a name to save, or press Enter to skip.

A profile stores only the file-independent settings: `mode`, `output_format`, `max_gap_s`, `gap_policy`, `ignore_big_gaps_m`, `dedupe`, `recompute_distance`, `sport`, and `timezone`. It never stores the input files, the output path, the per-channel sources (`--position-from`, `--hr-from`, and so on), or per-run flags like `--yes` and `--dry-run`. Those change every run, so baking them into a saved profile would be wrong.

## Reuse a profile

Pass `--profile <name>` and the saved settings apply. You still give the files and the output path, which are different every time:

```
gpsmerge run trainer.fit watch.tcx --out ride.tcx --profile trainer
```

This applies `--mode stack --format tcx --timezone Europe/Amsterdam` from the saved profile, so you do not have to type them.

## Flags override the profile

A flag you type on the command line always wins over the profile. This lets you reuse a profile but change one thing for a single run. For example, the `trainer` profile writes TCX, but here you want GPX just this once:

```
gpsmerge run trainer.fit watch.tcx --out ride.gpx --profile trainer --format gpx
```

The output is GPX. Everything else still comes from the profile. The rule is simple: **a flag you pass beats the profile; the profile beats the built-in default.**

## Manage your profiles

```
gpsmerge profile list            # show the profiles you have saved
gpsmerge profile show trainer    # print one profile's saved settings
gpsmerge profile rm trainer      # delete a profile
```

## Where profiles live

Profiles are stored as small TOML files, one per profile, in your platform's config directory under `gpsmerge/profiles`. On macOS that is `~/Library/Application Support/gpsmerge/profiles`, and on Linux it is usually `~/.config/gpsmerge/profiles`. You can open these files to read them, but the `gpsmerge profile` commands are the easiest way to manage them.
