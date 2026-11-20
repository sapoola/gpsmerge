# gpsmerge

Merge GPX, TCX, and FIT activity files into one clean file, entirely on your own machine. No upload, no account, no network.

`gpsmerge` is an offline, local-first reimplementation of the file-merging part of [gotoes.org](https://gotoes.org/tools/merge-gps-files). It does the two things people actually need:

- **Stack** two recordings of the *same* ride and take a different sensor from each: GPS from your watch, power from your trainer, heart rate from a chest strap.
- **Append** two *sequential* recordings into one continuous activity, for when your bike computer died mid-ride and you finished on your phone.

Run it with no arguments for a guided wizard, or pass flags for scripting.

## Status

Working and usable, ahead of a first published release. gpsmerge reads GPX, TCX, and FIT, writes GPX and TCX, and merges in three ways: `append` for sequential recordings, `stack` for two recordings of the same ride, and `auto` to let the time overlap decide. On the way through it can apply optional transforms (time shift, trim, elevation smoothing, stopped-time insertion), save and reuse settings as named profiles, and walk you through the whole thing with an interactive wizard.

## Install

Not yet published to a package index, so the current way to run it is from a local checkout with [uv](https://docs.astral.sh/uv/):

```bash
uv sync
uv run gpsmerge            # the guided wizard
uv run gpsmerge --version
uv run gpsmerge run --help
```

Once published, the intended install paths are `uv tool install gpsmerge`, `pipx install gpsmerge`, and a Homebrew tap (`brew tap <owner>/gpsmerge && brew install gpsmerge`). Until then, prefer the `uv run gpsmerge` path above.

## Quick start

Run `gpsmerge` with no arguments for the guided wizard, which detects whether your files stack or append, shows a preview, and writes only once you confirm. To merge directly, use `gpsmerge run` (the examples below assume the tool is on your `PATH`; from a checkout, prefix each with `uv run`):

```bash
# Append: a ride split across two recordings, into one GPX
gpsmerge run morning.gpx evening.gpx --out ride.gpx

# Stack: GPS from a watch, power and heart rate from a trainer
gpsmerge run watch.fit trainer.tcx --mode stack \
  --position-from watch.fit --power-from trainer.tcx --out ride.gpx

# Auto: let gpsmerge decide whether to append or stack
gpsmerge run a.gpx b.gpx --out merged.gpx

# Preview only, writing nothing
gpsmerge run a.gpx b.gpx --dry-run
```

Run `gpsmerge run --help` for the full set of flags (grouped into Output, Stacking, Appending, Transforms, Profiles, and Preview panels), `gpsmerge examples` for copy-pasteable commands, and `gpsmerge formats` for what each format can carry.

## Why not just use the website, or GPSBabel?

The gotoes.org tool is excellent but it is a web service: you upload files that contain your home address (in their start and end coordinates) and your health data to a third-party server. `gpsmerge` never sends your data anywhere. [GPSBabel](https://www.gpsbabel.org/) can also merge files, but it is a general-purpose converter with a verbose, flag-heavy CLI; `gpsmerge` is a focused, opinionated, timestamp-aware tool with a guided wizard and real sensor-channel merging.

## Documentation

Plain-language concept docs live in [`docs/`](docs/). Start with [`docs/concepts.md`](docs/concepts.md) for what "stack" and "append" mean.

## License

MIT. See [LICENSE](LICENSE).
