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

## Table of contents

- [What's new in v1.1.0](#whats-new-in-v110)
- [Output schema](#output-schema)
- [Quick start](#quick-start)
  - [1 — Clone and install](#1--clone-and-install)
  - [2 — Upstox credentials](#2--upstox-credentials)
  - [3 — Login for access token](#3--login-for-access-token)
  - [4 — First fetch](#4--first-fetch)
- [CLI reference](#cli-reference)
  - [fetch — historical candles](#fetch--historical-candles)
  - [batch — many instruments at once](#batch--many-instruments-at-once)
  - [update — incremental refresh](#update--incremental-refresh)
  - [intraday — today's candles](#intraday--todays-candles)
  - [plot — interactive charts](#plot--interactive-charts)
  - [validate — data quality](#validate--data-quality)
  - [find — interactive search](#find--interactive-search)
  - [cache — disk cache management](#cache--disk-cache-management)
  - [login / keys](#login--keys)
- [Python API](#python-api)
  - [Sync fetcher](#sync-fetcher)
  - [Async fetcher (recommended)](#async-fetcher-recommended)
  - [Multi-instrument batch](#multi-instrument-batch)
  - [Incremental updates](#incremental-updates)
  - [Validation](#validation)
  - [Charting](#charting)
- [Supported intervals](#supported-intervals)
- [Built-in instrument keys](#built-in-instrument-keys)
- [Project structure](#project-structure)
- [How features work (under the hood)](#how-features-work-under-the-hood)
- [PyCharm setup](#pycharm-setup)
- [Tests, linting, type checking](#tests-linting-type-checking)
- [Upgrading from v1.0](#upgrading-from-v10)
- [Troubleshooting](#troubleshooting)
- [Rate limits and caveats](#rate-limits-and-caveats)
- [Changelog](#changelog)
- [License](#license)

---

## What's new in v1.1.0

The backward-compatible feature release. Every v1.0 script still works — the sync `HistoricalFetcher` interface is preserved. But there's a lot of new power:

- **Async fetcher** — concurrent chunk downloads with `aiolimiter`-backed rate limiting. 5–10× faster for long date ranges.
- **Checkpoint resume** — every fetched chunk is persisted to disk. If your process dies mid-fetch, re-running picks up exactly where it left off.
- **Tenacity retries** — transient 5xx, 429, and network errors are retried with exponential backoff. No more single-flake failures on multi-hour fetches.
- **Incremental updates** — `upstox-fetch update ./data/file.parquet` fetches only the missing range since the last timestamp. Perfect for daily pipelines.
- **Batch mode** — fetch many instruments concurrently through a shared rate limiter. Backfill 20 stocks in the time it used to take for 2.
- **Disk-cached Bhav Copy** — NSE Bhav Copies cached as parquet under `~/.cache/upstox-historical/bhav/`. Second run of the same enrichment hits disk, not NSE.
- **Interactive Plotly charts** — candlesticks + volume + SMA/EMA/Bollinger/RSI/MACD/VWAP overlays, exported as standalone HTML.
- **Data validation** — OHLC sanity, gap detection against the NSE trading calendar, log-return outlier detection. Case-insensitive column resolution, so it works on any old or new save.
- **Interactive instrument search** — arrow-key navigable, clipboard copy on selection.
- **Two-stage Rich progress bars** — chunks + NSE enrichment, each showing elapsed and ETA.
- **Capitalized output columns** — saved files now use `Date, Open, High, Low, Close, Volume, VWAP` for consistency with standard financial-data conventions. See [Output schema](#output-schema).
- **Quieted httpx logging** — per-URL request logs only appear with `--verbose`.
- **Cache inspection** — `upstox-fetch cache stats | clear-bhav | clear-checkpoints`.

See [Upgrading from v1.0](#upgrading-from-v10) for the concrete upgrade path.

---

## Output schema

Saved files (CSV or Parquet) use these column names:

**Without `--nse-enrich`:**

```
Date, Open, High, Low, Close, Volume, VWAP, open_interest
```

**With `--nse-enrich`:**

```
Date, Symbol, Series, Prev Close, Open, High, Low, Close, Volume, VWAP,
Turnover, Trades, Deliverable Volume, %Deliverble
```

> **Note:** v1.0 used lowercase OHLCV (`open, high, low, close, volume, vwap`). v1.1 capitalizes these on save. If your downstream code reads lowercase column names, add a rename shim: `df.rename(columns=str.lower)` after loading. See [Upgrading from v1.0](#upgrading-from-v10).

In-memory DataFrames returned by the Python API keep lowercase OHLCV during processing; the rename happens only at save time. So if you use `HistoricalFetcher.fetch()` and work with the DataFrame directly (without saving), you get lowercase columns — same as v1.0.

---

## Quick start

### 1 — Clone and install

```bash
git clone git@github.com:KosmicByte/upstox-historical.git
cd upstox-historical

uv venv                              # creates .venv/
uv sync                              # installs all deps from pyproject.toml
uv sync --extra dev                  # also installs pytest, ruff, mypy, ipykernel
```

### 2 — Upstox credentials

1. Go to [https://account.upstox.com/developer/apps](https://account.upstox.com/developer/apps) and create an app.
2. Note your **API Key** and **API Secret**.
3. Set the redirect URI to `http://localhost:8000/callback` (or any URL you own).
4. Copy the template `.env`:

   ```bash
   cp .env.example .env
   # edit .env and fill in UPSTOX_API_KEY and UPSTOX_API_SECRET
   ```

### 3 — Login for access token

```bash
uv run upstox-fetch login
```

This opens a browser, logs you in to Upstox, and prints your access token. Paste it into `.env`:

```env
UPSTOX_ACCESS_TOKEN=your_token_here
```

> **Note:** Upstox access tokens expire daily at ~3:30 AM IST. Re-run `upstox-fetch login` each morning to refresh.

### 4 — First fetch

```bash
# Fetch one year of Nifty 50 daily candles as Parquet
uv run upstox-fetch fetch "NSE_INDEX|Nifty 50" \
    --interval day \
    --from 2024-01-01 \
    --to   2024-12-31 \
    --format parquet

# Validate the result
uv run upstox-fetch validate ./data/NSE_INDEX_Nifty_50_day_2024-01-01_2024-12-31.parquet

# Render an interactive chart with 20/50/200-day SMAs and RSI
uv run upstox-fetch plot ./data/NSE_INDEX_Nifty_50_day_2024-01-01_2024-12-31.parquet \
    --indicators sma20,sma50,sma200,rsi14 --open
```

Done — three commands and you've got verified data plus a browsable chart.

---

## CLI reference

All commands are accessed via the `upstox-fetch` entry point. Run `upstox-fetch <command> --help` for complete options.

Global flags:
- `--verbose` / `-v` — enable DEBUG logging (re-enables per-URL httpx logs)
- `--version` — print version and exit

### `fetch` — historical candles

Fetches OHLCV candles for a single instrument over any date range. Async and concurrent by default; falls back to sync with `--sync`.

```bash
uv run upstox-fetch fetch "NSE_INDEX|Nifty 50" \
    --interval day \
    --from 2020-01-01 \
    --to 2025-10-31 \
    --format parquet \
    --out-dir ./data
```

**Options:**

| Flag | Default | Description |
|------|---------|-------------|
| `--interval` `-i` | `day` | Candle interval (see [intervals](#supported-intervals)) |
| `--from` `-f` | `2024-01-01` | Start date YYYY-MM-DD |
| `--to` `-t` | `2024-12-31` | End date YYYY-MM-DD |
| `--out-dir` `-o` | `./data` | Output directory |
| `--format` | `parquet` | `csv` or `parquet` |
| `--sync` | off | Use the sync fetcher (for debugging) |
| `--nse-enrich` | off | Add NSE Bhav Copy columns |
| `--symbol` `-s` | auto | NSE ticker for enrichment (e.g. `RELIANCE`) |
| `--series` | auto | NSE series for enrichment (e.g. `EQ`) |
| `--preview/--no-preview` | preview | Print first 10 rows after fetch |
| `--validate/--no-validate` | validate | Run quality checks after fetch |

**NSE-enriched fetch for Reliance:**

```bash
uv run upstox-fetch fetch "NSE_EQ|INE002A01018" \
    -i day -f 2024-01-01 -t 2024-12-31 \
    --nse-enrich --symbol RELIANCE --series EQ \
    --format parquet
```

> For ISIN-keyed equities, always pass `--symbol` explicitly. NSE Bhav Copy is keyed by ticker (`RELIANCE`), not ISIN (`INE002A01018`), so the enrichment can't auto-derive the ticker.

### `batch` — many instruments at once

Fetches many tickers concurrently, sharing a single rate-limit budget. Builtin instrument keys (see [below](#built-in-instrument-keys)) are resolved automatically; unknown tickers are assumed to be `NSE_EQ|<TICKER>`.

```bash
uv run upstox-fetch batch \
    --symbols RELIANCE,TCS,INFY,HDFCBANK,ICICIBANK \
    --interval day \
    --from 2024-01-01 --to 2024-12-31 \
    --nse-enrich \
    --format parquet
```

A summary table prints after completion showing row counts and per-instrument validation status.

### `update` — incremental refresh

Reads the last timestamp from a saved file, fetches only the missing range to today (or `--until`), and writes back in place. Auto-detects NSE enrichment from the existing columns. Auto-renames the file to reflect the new date range.

```bash
# Refresh to today
uv run upstox-fetch update ./data/NSE_EQ_INE002A01018_day_2020-01-01_2025-10-31.parquet

# Preview what would be fetched
uv run upstox-fetch update ./data/...parquet --dry-run

# Refresh up to a specific date
uv run upstox-fetch update ./data/...parquet --until 2025-12-31
```

**Use this for your daily cron job.** A single command brings your entire dataset current — no need to refetch history.

### `intraday` — today's candles

```bash
# Today's 1-minute Nifty candles
uv run upstox-fetch intraday "NSE_INDEX|Nifty 50"

# Today's 5-minute BankNifty (resampled from 1-min)
uv run upstox-fetch intraday "NSE_INDEX|Nifty Bank" --interval 5minute
```

### `plot` — interactive charts

Renders a standalone HTML with candlesticks, volume, and any combination of indicators. Opens in a browser on request. Reads capitalized or lowercase column files transparently.

```bash
uv run upstox-fetch plot ./data/reliance.parquet \
    --indicators sma20,sma50,bb20,rsi14,macd,vwap_overlay \
    --open
```

**Available indicators** (comma-separated):

| Spec | Meaning |
|------|---------|
| `sma20`, `sma50`, `sma200` | Simple moving average, N periods |
| `ema12`, `ema26` | Exponential moving average, N periods |
| `bb20` | Bollinger Bands (SMA ± 2σ over N periods) |
| `rsi14` | Relative Strength Index over N periods (subplot) |
| `macd` | MACD (12, 26, 9) with signal + histogram (subplot) |
| `vwap_overlay` | Overlay the `VWAP` column if present |

Output defaults to `<filename>.html` next to the source.

### `validate` — data quality

Runs the full quality-check suite on a saved file. Case-insensitive — works on v1.0 and v1.1 files equally.

```bash
uv run upstox-fetch validate ./data/reliance.parquet
```

Checks include:
- OHLC ordering (high ≥ open, close ≥ low ≥ 0)
- Non-negative and zero-volume counts
- Duplicate and non-monotonic timestamps
- Missing trading days (cross-referenced with NSE Bhav Copy cache)
- Log-return outliers (z-score > 8σ by default)

Output distinguishes **errors** (almost certainly bad data) from **warnings** (possibly legitimate, worth knowing). Zero errors and zero warnings = `✓ clean`.

### `find` — interactive search

```bash
uv run upstox-fetch find             # prompts for query
uv run upstox-fetch find reliance    # prefills query, shows results
```

Arrow keys to navigate, Enter to select. The chosen `instrument_key` is copied to your clipboard automatically (if a clipboard backend is available).

Non-interactive mode prints results as a table:

```bash
uv run upstox-fetch find reliance --non-interactive
```

### `cache` — disk cache management

```bash
uv run upstox-fetch cache stats            # show cache contents + size
uv run upstox-fetch cache clear-bhav       # wipe Bhav Copy cache
uv run upstox-fetch cache clear-checkpoints # wipe fetch checkpoints
uv run upstox-fetch cache clear-all        # nuke everything
```

Cache location: `$UPSTOX_CACHE_DIR`, or `~/.cache/upstox-historical/` (Linux/Mac), or `%LOCALAPPDATA%/upstox-historical/cache/` (Windows).

### `login` / `keys`

```bash
uv run upstox-fetch login                  # OAuth flow → prints access token
uv run upstox-fetch keys                   # list built-in NSE instrument keys
```

---

## Python API

### Sync fetcher

Identical to v1.0. **Note:** the in-memory DataFrame returned by `fetch()` still has lowercase OHLCV column names — capitalization happens only at save time.

```python
from upstox_historical.fetcher import HistoricalFetcher
from upstox_historical.instruments import NSE
from upstox_historical.models import Interval

fetcher = HistoricalFetcher()

df = fetcher.fetch(
    instrument_key=NSE.NIFTY_50,
    interval=Interval.D1,
    from_date="2024-01-01",
    to_date="2024-12-31",
)
print(df.tail())                     # lowercase columns

path = fetcher.fetch_and_save(
    instrument_key=NSE.NIFTY_50,
    interval=Interval.D1,
    from_date="2024-01-01",
    to_date="2024-12-31",
    fmt="parquet",
)                                    # file has capitalized columns
```

### Async fetcher (recommended)

For anything longer than a single chunk, use the async fetcher. It's concurrent, resumable, and mostly a drop-in API change.

```python
import asyncio
from upstox_historical.async_fetcher import AsyncHistoricalFetcher
from upstox_historical.instruments import NSE

async def main():
    fetcher = AsyncHistoricalFetcher()
    df = await fetcher.fetch(
        instrument_key=NSE.RELIANCE,
        interval="5minute",
        from_date="2020-01-01",
        to_date="2025-10-31",
        nse_enrich=True,
        symbol="RELIANCE",
    )
    print(df.shape)

asyncio.run(main())
```

### Multi-instrument batch

```python
import asyncio
from upstox_historical.async_fetcher import AsyncHistoricalFetcher
from upstox_historical.instruments import NSE

async def main():
    fetcher = AsyncHistoricalFetcher()
    results = await fetcher.fetch_many(
        instrument_keys=[NSE.RELIANCE, NSE.TCS, NSE.INFY, NSE.HDFC_BANK],
        interval="day",
        from_date="2024-01-01",
        to_date="2024-12-31",
        nse_enrich=True,
        symbols={
            NSE.RELIANCE: "RELIANCE",
            NSE.TCS: "TCS",
            NSE.INFY: "INFY",
            NSE.HDFC_BANK: "HDFCBANK",
        },
    )
    for key, df in results.items():
        print(f"{key}: {len(df)} rows")

asyncio.run(main())
```

### Incremental updates

```python
import asyncio
from upstox_historical.updater import plan_update, update

plan = plan_update("./data/NSE_EQ_INE002A01018_day_2020-01-01_2025-03-31.parquet")
print(f"Need to fetch {plan.new_from} to {plan.new_to}")

result = asyncio.run(update("./data/NSE_EQ_INE002A01018_day_2020-01-01_2025-03-31.parquet"))
print(f"Updated to {result.path}")
```

### Validation

```python
from upstox_historical.validation import validate, repair

report = validate(df)                # accepts any column casing
print(report.summary())

if report.errors:
    df = repair(df)                  # best-effort cleanup
```

`ValidationReport` exposes structured fields: `errors`, `warnings`, `ohlc_violations`, `zero_volume_rows`, `missing_trading_days`, `outlier_rows`, etc.

### Charting

```python
from upstox_historical.plotting import plot_candles

path = plot_candles(
    "./data/reliance.parquet",
    indicators=["sma20", "sma50", "bb20", "rsi14", "macd"],
    out="./charts/reliance.html",
    title="Reliance Industries",
    open_browser=True,
)
```

---

## Supported intervals

| Flag value | Native? | Description |
|------------|---------|-------------|
| `1minute`  | native  | 1-minute candle |
| `2minute`  | resampled | built from 1-min |
| `3minute`  | resampled | built from 1-min |
| `5minute`  | resampled | built from 1-min |
| `10minute` | resampled | built from 1-min |
| `15minute` | resampled | built from 1-min |
| `20minute` | resampled | built from 1-min |
| `25minute` | resampled | built from 1-min |
| `30minute` | native  | 30-minute candle |
| `day`      | native  | daily (default) |
| `week`     | native  | weekly |
| `month`    | native  | monthly |

---

## Built-in instrument keys

Access via `upstox_historical.instruments.NSE.*`:

| Constant | Instrument key |
|----------|----------------|
| `NSE.NIFTY_50`  | `NSE_INDEX\|Nifty 50` |
| `NSE.BANKNIFTY` | `NSE_INDEX\|Nifty Bank` |
| `NSE.FINNIFTY`  | `NSE_INDEX\|Nifty Fin Service` |
| `NSE.INDIA_VIX` | `NSE_INDEX\|India VIX` |
| `NSE.RELIANCE`  | `NSE_EQ\|INE002A01018` |
| `NSE.TCS`       | `NSE_EQ\|INE467B01029` |
| `NSE.INFY`      | `NSE_EQ\|INE009A01021` |
| … and more      | run `upstox-fetch keys` |

---

## Project structure

```
upstox-historical/
├── src/
│   └── upstox_historical/
│       ├── __init__.py             Package exports + version
│       ├── auth.py                 OAuth login helper
│       ├── cli.py                  Typer CLI (upstox-fetch)
│       ├── config.py               Settings + logging setup
│       ├── instruments.py          NSE instrument key constants
│       ├── models.py               Pydantic response models
│       │
│       ├── client.py               Sync HTTP client (tenacity retries)
│       ├── async_client.py        ⦿ Async HTTP client (rate-limited)
│       │
│       ├── fetcher.py              Sync fetcher (chunked + checkpoints)
│       ├── async_fetcher.py       ⦿ Async concurrent fetcher
│       │
│       ├── nse_enrichment.py       Bhav Copy enrichment (disk-cached)
│       │
│       ├── cache.py               ⦿ Disk cache (Bhav + checkpoints)
│       ├── updater.py             ⦿ Incremental update logic
│       ├── validation.py          ⦿ Quality checks (case-insensitive)
│       ├── plotting.py            ⦿ Plotly charting
│       └── search.py              ⦿ Interactive instrument search
│
├── tests/
│   └── test_fetcher.py
├── examples/
│   └── basic_fetch.py
├── .env.example
├── .gitignore
├── pyproject.toml
├── README.md
└── uv.lock
```

Modules marked `⦿` are new in v1.1.0.

---

## How features work (under the hood)

### Chunking

Upstox caps per-request date ranges by interval:

| Interval | Max per request |
|----------|-----------------|
| 1minute  | 1 month |
| 30minute | 1 year |
| day      | 1 year |
| week     | 10 years |
| month    | 10 years |

Both fetchers automatically split long ranges into chunks, fetch each, and stitch the result.

### Checkpoint resume

Every completed chunk is persisted as a parquet under
`~/.cache/upstox-historical/checkpoints/<job-hash>/chunk_NNNN.parquet`. The job hash is a stable SHA-256 of `(instrument_key, interval, from_date, to_date)`.

If a fetch crashes, the next invocation with identical args:
1. Detects the checkpoint directory
2. Loads completed chunks from disk
3. Only fetches the missing chunks
4. On successful completion, wipes the checkpoint directory

### Bhav Copy disk cache

Each parsed Bhav Copy is cached as a parquet under `~/.cache/upstox-historical/bhav/YYYY-MM-DD.parquet`. Confirmed non-trading days (404 from NSE) get a zero-byte `.miss` marker so we don't re-hit NSE on weekends/holidays. First enrichment of a date range is slow; subsequent enrichments of the same or overlapping ranges are near-instant.

### Rate limiting and retries

The async client wraps every request in two layers:

1. **`aiolimiter.AsyncLimiter(20, 1.0)`** — max 20 requests per second, globally.
2. **`asyncio.Semaphore(5)`** — caps in-flight concurrency.

Transient failures (5xx, 429, timeouts, connection errors) are retried via `tenacity` with exponential backoff (1s → 2s → 4s → 8s, capped at 30s, 5 attempts total). 401 is never retried — the token needs refreshing via `upstox-fetch login`.

### Column casing

- **During processing** (in-memory DataFrames, API parsing, resampling, VWAP computation): lowercase `open, high, low, close, volume, vwap`. Matches Upstox API field names.
- **At save time** (`_save` inside `fetcher.py`): capitalized — `Date, Open, High, Low, Close, Volume, VWAP`.
- **Enrichment step** (`nse_enrichment._finalise_columns`): performs the same capitalization, so enriched output is pre-capitalized before `_save` sees it.

This keeps internal processing simple while providing a consistent, conventional schema on disk.

### NSE Bhav Copy enrichment

Adds columns matching the `Sample.csv` layout:

| Column | Source |
|--------|--------|
| Symbol | User-specified or derived from instrument_key |
| Series | User-specified or derived from prefix (NSE_EQ → EQ) |
| Prev Close | NSE Bhav Copy `PREV_CLOSE` |
| Turnover | Computed locally: `VWAP × Volume` |
| Trades | NSE Bhav Copy `NO_OF_TRADES` |
| Deliverable Volume | NSE Bhav Copy `DELIV_QTY` |
| %Deliverble | NSE Bhav Copy `DELIV_PER` |

For intraday candles, day-level Bhav Copy values are broadcast to every intraday row of that date. The `timestamp` column is renamed to `Date` after enrichment.

---

## PyCharm setup

1. Open the project folder in PyCharm.
2. **Settings → Python Interpreter → Add → Existing** → point to `.venv/bin/python`.
3. (Optional) Add a `.env` plugin so PyCharm loads the vars for run configs.

---

## Tests, linting, type checking

```bash
uv run pytest tests/ -v
uv run ruff check src/
uv run mypy src/
```

---

## Upgrading from v1.0

Drop-in upgrade. Your existing scripts using `HistoricalFetcher` continue to work.

```bash
cd ~/upstox_historical
git pull                             # if the new files are in the remote
uv sync                              # pulls new deps
uv run upstox-fetch --help           # should show 10 commands now
```

**One behaviour change worth knowing about:** saved files now have capitalized OHLCV column names. If you have downstream code reading v1.0 files, the simplest fix is to lowercase on read:

```python
df = pd.read_parquet("your_file.parquet").rename(columns=str.lower)
# now df has 'open', 'high', 'low', 'close' etc. as before
```

Validation, updater, and plotting are all case-insensitive, so they work transparently on files from either version.

**New modules you can opt into at your own pace:**
- Replace sync `HistoricalFetcher` calls with `AsyncHistoricalFetcher` wrapped in `asyncio.run()` for 5–10× speedup.
- Add `upstox-fetch update` to your daily workflow.
- Add `upstox-fetch validate` to catch data issues before they reach downstream consumers (e.g. SPDE calibration).

---

## Troubleshooting

**`401 Unauthorised` on first request.** Your access token expired. Run `upstox-fetch login` to get a fresh one.

**`429 Rate limit exceeded` warnings in logs.** The retry layer is handling it. If you see it constantly, lower `rate_limit` in `AsyncUpstoxClient(...)` from 20 to 10.

**`Missing OHLC columns: ['close', 'high', 'low', 'open']`.** You're running an older `validation.py` that expects lowercase. Upgrade to v1.1.0 — validation is now case-insensitive.

**Fetch hangs or runs slowly.** Set `--verbose` to see per-chunk progress. If it's stuck at "Fetching", it's likely rate-limit backoff — wait 30 seconds.

**Bhav Copy 404 for recent dates.** NSE publishes the Bhav Copy ~1 hour after market close. Dates before today-1 should always be available.

**Plotly chart shows no candles.** Run `upstox-fetch validate` on the file first — the file may be empty or malformed.

**Update rewrites with a different filename.** By design — the filename encodes the date range, so extending the range changes the name. Use `rename_output=False` in the Python API for stable filenames.

**Where are my cached files?** `upstox-fetch cache stats` prints the location.

---

## Rate limits and caveats

- Historical candles: Upstox allows bursty short-term traffic but aggressive sustained load gets 429. The built-in rate limiter (20 req/s) is conservative; raise it if your tier allows more.
- Access tokens expire **daily** at ~3:30 AM IST. Automate `upstox-fetch login` or re-run each morning.
- 1-minute data is available for the **last ~2 years**. Longer ranges return partial results.
- Daily/weekly/monthly data goes much further back (varies by instrument).
- NSE Bhav Copy rate: NSE's archive is bot-sensitive. The client adds a 1-second delay between live Bhav Copy fetches; once cached, it's instant.

---

## Changelog

### v1.1.0 (Apr 18, 2026)

**New features:**
- Async concurrent fetcher (`AsyncHistoricalFetcher`) with `aiolimiter` rate limiting and `asyncio.Semaphore`-based concurrency cap
- Checkpoint resume for interrupted fetches (per-chunk parquet cache)
- Incremental `update` command — fetch only what's missing since the last timestamp
- Multi-instrument `batch` command — fetch many tickers concurrently through a shared rate limiter
- `plot` command with Plotly candlestick charts + SMA/EMA/Bollinger/RSI/MACD/VWAP overlays
- `validate` command with OHLC sanity, gap detection, outlier detection (case-insensitive)
- `find` command — interactive instrument search with clipboard copy
- `cache` command — inspect and clear Bhav Copy / checkpoint caches
- Tenacity retries on transient HTTP errors (5xx, 429, network)
- Disk-cached NSE Bhav Copy with `.miss` markers for non-trading days
- Two-stage Rich progress bars (chunks + NSE enrichment)

**Behaviour changes:**
- Output files now use capitalized column names: `Date, Open, High, Low, Close, Volume, VWAP` (plus the existing capitalized NSE columns when enriched)
- httpx per-URL request logs suppressed at INFO level; re-enabled with `--verbose`
- Saved filename still encodes date range; `update` renames file when extending the range

**Backward compatibility:**
- Sync `HistoricalFetcher` interface unchanged — all v1.0 scripts work
- Validation, plotting, and updater modules handle both lowercase (v1.0) and capitalized (v1.1) column names transparently

### v1.0.0 (earlier)

- Data fetch only: OHLCV + NSE Bhav Copy enrichment
- Sync fetcher with auto-chunking
- CLI: fetch, intraday, login, keys

---

## License

MIT
