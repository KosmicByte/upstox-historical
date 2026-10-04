# Changelog

## v1.1.0 — 2026-04-18

### Added

- `AsyncHistoricalFetcher`: concurrent chunk fetch, `aiolimiter` rate limit, `asyncio.Semaphore` concurrency cap
- Checkpoint resume per chunk
- `tenacity` retries on 5xx, 429, network errors
- `update`: incremental fetch from last timestamp
- `batch`: concurrent multi-instrument fetch, shared rate limit
- `plot`: Plotly candlesticks with SMA, EMA, Bollinger, RSI, MACD, VWAP
- `validate`: OHLC sanity, gap detection, outlier detection
- `find`: interactive instrument search, clipboard copy
- `cache`: inspect and clear Bhav Copy and checkpoint caches
- Disk-cached Bhav Copy with `.miss` markers for non-trading days
- Two-stage progress bars (chunks, enrichment)

### Changed

- Saved files use capitalized columns: `Date, Open, High, Low, Close, Volume, VWAP`
- httpx per-URL logs require `--verbose`
- `update` renames the file to the extended date range

### Compatibility

- `HistoricalFetcher` interface unchanged
- Validation, plotting, and updater accept lowercase and capitalized columns

### Upgrade

```bash
git pull
uv sync
uv run upstox-fetch --help    # 10 commands
```

Code reading v1.0 files by lowercase name:

```python
df = pd.read_parquet("file.parquet").rename(columns=str.lower)
```

## v1.0.0

- OHLCV fetch with NSE Bhav Copy enrichment
- Sync fetcher with auto-chunking
- CLI: `fetch`, `intraday`, `login`, `keys`
