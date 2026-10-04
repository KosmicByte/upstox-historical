<p align="center">
  <img src="assets/logo.svg" alt="upstox-historical" width="120" />
</p>

<h1 align="center">upstox-historical</h1>

<p align="center">
  Historical OHLCV data from the <b>Upstox v2 API</b>: fetch, update, validate, chart.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python" />
  <img src="https://img.shields.io/badge/version-1.1.0-brightgreen.svg" alt="Version" />
  <img src="https://img.shields.io/badge/license-MIT-green.svg" alt="License" />
</p>

---

## About

Data layer for Indian equities research. Handles range chunking, rate limiting, retries, crash resume, and NSE Bhav Copy enrichment (delivery, turnover, trade count). Output: CSV or Parquet.

## Install

```bash
git clone git@github.com:KosmicByte/upstox-historical.git
cd upstox-historical
uv sync
```

## Usage

```bash
uv run upstox-fetch login

uv run upstox-fetch fetch "NSE_INDEX|Nifty 50" \
    --interval day --from 2024-01-01 --to 2024-12-31 --format parquet

uv run upstox-fetch plot ./data/NSE_INDEX_Nifty_50_day_2024-01-01_2024-12-31.parquet \
    --indicators sma20,sma50,rsi14 --open
```

## Documentation

| Doc | Contents |
|-----|----------|
| [Quick start](./docs/quickstart.md) | Install, credentials, first fetch |
| [CLI reference](./docs/cli.md) | Commands, flags, intervals, instrument keys |
| [Python API](./docs/api.md) | Fetchers, batching, updates, validation, plotting |
| [Design](./docs/design.md) | Schema, internals, project layout |
| [Troubleshooting](./docs/troubleshooting.md) | Errors and limits |
| [Roadmap](./docs/roadmap.md) | Planned work and scope |
| [FAQ](./docs/faq.md) | Design rationale |
| [Changelog](./docs/changelog.md) | Releases and upgrade notes |
| [Authors](./docs/authors.md) | Maintainer and dependencies |

## License

MIT
