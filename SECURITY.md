# Security policy

gpsmerge runs entirely on your own machine — it has no network, no account, and no server component. The main security surface is therefore the **file parsing**: gpsmerge reads GPX and TCX (XML) and FIT (binary) files that may come from third parties (a friend's ride export, a download, a tool you don't control). Treat every input file as untrusted.

## Reporting a vulnerability

Please report suspected vulnerabilities privately rather than opening a public issue. Email **security@gpsmerge.invalid** (placeholder — replace with a monitored address before release) with:

- a description of the issue and its impact,
- the smallest input file or command that reproduces it, and
- the gpsmerge version (`gpsmerge --version`) and your OS and Python version.

We aim to acknowledge a report within a few working days and to coordinate a fix and disclosure with you. Please give us a reasonable window to release a fix before any public disclosure.

## Supported versions

gpsmerge is pre-1.0. Until the first stable release, security fixes are made on the latest released version only. Once 1.0 ships, this table will name the supported lines.

| Version | Supported |
| ------- | --------- |
| latest  | yes       |
| older   | no        |

## Hardening notes

**Untrusted XML (GPX/TCX) and XXE.** GPX and TCX are XML, so the classic risks are XML External Entity (XXE) expansion, entity-expansion denial of service ("billion laughs"), and external-resource fetches via DOCTYPE/DTD. gpsmerge parses XML with [lxml](https://lxml.de/); by default lxml does **not** resolve external entities or load network resources during parsing, so a malicious `SYSTEM`/`PUBLIC` entity cannot exfiltrate local files or reach out over the network. Because the parser is a key part of this surface, gpsmerge pins a recent lxml floor (currently `lxml>=6.1.0`, which is past CVE-2026-41066); keep it up to date.

**Untrusted binary (FIT).** FIT is a binary format decoded by `fitdecode`. Malformed files should fail with a clear error rather than crash; if you find an input that hangs, exhausts memory, or escapes the parser, please report it as above.

**No code execution from inputs.** gpsmerge never evaluates, imports, or executes anything found inside an activity file; inputs are only ever read as data.
