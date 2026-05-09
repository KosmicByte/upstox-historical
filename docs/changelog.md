# Changelog & upgrade guide

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
- **Capitalized output columns** — saved files now use `Date, Open, High, Low, Close, Volume, VWAP` for consistency with standard financial-data conventions. See [design.md](./design.md).
- **Quieted httpx logging** — per-URL request logs only appear with `--verbose`.
- **Cache inspection** — `upstox-fetch cache stats | clear-bhav | clear-checkpoints`.

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

## Version history

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
