<h1 align="center">kipris-nol</h1>

<p align="center">
  <em>Know where every trademark and patent application stands — in one batch.</em>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/status-proof--of--concept-orange" alt="status">
  <img src="https://img.shields.io/badge/python-3.11+-3776AB?logo=python&logoColor=white" alt="python">
  <img src="https://img.shields.io/badge/data-KIPRIS%20Plus-1f3a5f" alt="kipris plus">
  <img src="https://img.shields.io/badge/cost-free%20tier-2f7d4f" alt="free tier">
</p>

---

Feed `kipris-nol` a list of application numbers and it returns each one's **current administrative disposition** — registered, rejected, withdrawn, under examination — as a clean snapshot you can diff over time.

Built on the official **KIPRIS Plus** open API (Korean Intellectual Property Rights Information Service).

## Highlights

- **Application number in → administrative disposition out.**
- **Point-in-time snapshot**, built to run on a schedule (monthly by default).
- **Free.** Runs entirely within the KIPRIS Plus free tier.
- **CSV + JSON** output.
- **Roadmap:** a cross-platform desktop app (`.exe` / `.dmg`) for non-technical users.

## Quick start

```bash
cp .env.example .env          # add your KIPRIS Plus accessKey
python -m kipris_nol          # reads testSet.json, writes CSV+JSON to out/
# or, explicitly:
python -m kipris_nol --input testSet.json --out-dir out --format both
```

No third-party dependencies — standard-library Python only.

## Status

Proof of concept. The API path is verified, the CLI is implemented, and an end-to-end run over the sample set passes.

- API reference — [`docs/index.html`](docs/index.html)
- KIPRIS Plus — https://plus.kipris.or.kr/
