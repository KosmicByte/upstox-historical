<p align="center">
  <img src="assets/logo.svg" alt="upstox-historical" width="120" />
</p>

<h1 align="center">upstox-historical</h1>

<p align="center">
  Fetch, update, chart, and validate historical OHLCV data from the <b>Upstox v2 API</b><br>
  Clean DataFrames · CSV/Parquet output · full CLI · async + interactive
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python" />
  <img src="https://img.shields.io/badge/version-1.1.0-brightgreen.svg" alt="Version" />
  <img src="https://img.shields.io/badge/license-MIT-green.svg" alt="License" />
</p>

---
## About

`upstox-historical` turns the Upstox v2 API into a reliable data layer for Indian equities research. It handles the boring parts — chunking long date ranges, rate limiting, retrying transient failures, resuming after crashes, and enriching OHLCV with NSE Bhav Copy fields (deliverables, turnover, trade count) — so your DataFrames come out clean and your pipelines stay reproducible.

Built for quantitative research and daily data jobs that can't afford flaky fetches.


## Install

```bash
git clone git@github.com:KosmicByte/upstox-historical.git
cd upstox-historical
uv sync
```

## Hello, candles

```bash
# Refresh your Upstox access token (daily)
uv run upstox-fetch login

# Fetch one year of Nifty 50 daily candles
uv run upstox-fetch fetch "NSE_INDEX|Nifty 50" \
    --interval day --from 2024-01-01 --to 2024-12-31 --format parquet

# Render an interactive chart
uv run upstox-fetch plot ./data/NSE_INDEX_Nifty_50_day_2024-01-01_2024-12-31.parquet \
    --indicators sma20,sma50,rsi14 --open
```

The complete walkthrough — credentials, environment, common pitfalls — lives in [docs/quickstart.md](./docs/quickstart.md).

---

## Documentation

| Doc | What's in it                                                                           |
|-----|----------------------------------------------------------------------------------------|
| [Quick start](./docs/quickstart.md) | Install, credentials, first fetch — the 5-minute path                                  |
| [CLI reference](./docs/cli.md) | Every command, every flag; intervals & built-in instrument keys                        |
| [Python API](./docs/api.md) | `HistoricalFetcher`, `AsyncHistoricalFetcher`, batching, validation, plotting          |
| [Design](./docs/design.md) | Output schema, internals (chunking, checkpoints, caching, rate limits), project layout |
| [Troubleshooting](./docs/troubleshooting.md) | Common errors, rate-limit caveats, token refresh                                       |
| [Roadmap](./docs/roadmap.md) | Where the project is heading and what's deliberately out of scope                      |
| [FAQ](./docs/faq.md) | Design-choice rationale and recurring questions                                        |
| [Changelog](./docs/changelog.md) | What's new in v1.1.0, upgrading from v1.0, full version history                        |
| [Authors](./docs/authors.md) | Maintainer, acknowledgments                                                            |
---

## License

MIT
