# gpsmerge

Merge GPX, TCX, and FIT activity files into one clean file — entirely on your own machine. No upload, no account, no network.

`gpsmerge` is an offline, local-first reimplementation of the file-merging part of [gotoes.org](https://gotoes.org/tools/merge-gps-files). It does the two things people actually need:

- **Stack** two recordings of the *same* ride and take a different sensor from each — GPS from your watch, power from your trainer, heart rate from a chest strap.
- **Append** two *sequential* recordings into one continuous activity — your bike computer died mid-ride and you finished on your phone.

Run it with no arguments for a guided wizard, or pass flags for scripting.

## Status

Early development. See the build plan and milestones in the repository; the first usable release reads GPX/TCX/FIT and writes GPX/TCX.

## Install

Not yet published. For local development:

```bash
poetry install
poetry run gpsmerge --version
```

Once released, the intended install paths are `pipx install gpsmerge`, `uv tool install gpsmerge`, and a Homebrew tap (`brew tap <owner>/gpsmerge && brew install gpsmerge`).

## Why not just use the website, or GPSBabel?

The gotoes.org tool is excellent but it is a web service: you upload files that contain your home address (in their start and end coordinates) and your health data to a third-party server. `gpsmerge` never sends your data anywhere. [GPSBabel](https://www.gpsbabel.org/) can also merge files, but it is a general-purpose converter with a verbose, flag-heavy CLI; `gpsmerge` is a focused, opinionated, timestamp-aware tool with a guided wizard and real sensor-channel merging.

## Documentation

Plain-language concept docs live in [`docs/`](docs/) — start with [`docs/concepts.md`](docs/concepts.md) for what "stack" and "append" mean.

## License

MIT — see [LICENSE](LICENSE).
