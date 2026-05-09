# Design

Output schema, internal mechanics, and project layout.

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

> **Note:** v1.0 used lowercase OHLCV (`open, high, low, close, volume, vwap`). v1.1 capitalizes these on save. If your downstream code reads lowercase column names, add a rename shim: `df.rename(columns=str.lower)` after loading. See [changelog.md](./changelog.md).

In-memory DataFrames returned by the Python API keep lowercase OHLCV during processing; the rename happens only at save time. So if you use `HistoricalFetcher.fetch()` and work with the DataFrame directly (without saving), you get lowercase columns — same as v1.0.

---

## Chunking

Upstox caps per-request date ranges by interval:

| Interval | Max per request |
|----------|-----------------|
| 1minute  | 1 month |
| 30minute | 1 year |
| day      | 1 year |
| week     | 10 years |
| month    | 10 years |

Both fetchers automatically split long ranges into chunks, fetch each, and stitch the result.

---

## Checkpoint resume

Every completed chunk is persisted as a parquet under
`~/.cache/upstox-historical/checkpoints/<job-hash>/chunk_NNNN.parquet`. The job hash is a stable SHA-256 of `(instrument_key, interval, from_date, to_date)`.

If a fetch crashes, the next invocation with identical args:
1. Detects the checkpoint directory
2. Loads completed chunks from disk
3. Only fetches the missing chunks
4. On successful completion, wipes the checkpoint directory

---

## Bhav Copy disk cache

Each parsed Bhav Copy is cached as a parquet under `~/.cache/upstox-historical/bhav/YYYY-MM-DD.parquet`. Confirmed non-trading days (404 from NSE) get a zero-byte `.miss` marker so we don't re-hit NSE on weekends/holidays. First enrichment of a date range is slow; subsequent enrichments of the same or overlapping ranges are near-instant.

---

## Rate limiting and retries

The async client wraps every request in two layers:

1. **`aiolimiter.AsyncLimiter(20, 1.0)`** — max 20 requests per second, globally.
2. **`asyncio.Semaphore(5)`** — caps in-flight concurrency.

Transient failures (5xx, 429, timeouts, connection errors) are retried via `tenacity` with exponential backoff (1s → 2s → 4s → 8s, capped at 30s, 5 attempts total). 401 is never retried — the token needs refreshing via `upstox-fetch login`.

---

## Column casing

- **During processing** (in-memory DataFrames, API parsing, resampling, VWAP computation): lowercase `open, high, low, close, volume, vwap`. Matches Upstox API field names.
- **At save time** (`_save` inside `fetcher.py`): capitalized — `Date, Open, High, Low, Close, Volume, VWAP`.
- **Enrichment step** (`nse_enrichment._finalise_columns`): performs the same capitalization, so enriched output is pre-capitalized before `_save` sees it.

This keeps internal processing simple while providing a consistent, conventional schema on disk.

---

## NSE Bhav Copy enrichment

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

## Development

```bash
uv run pytest tests/ -v
uv run ruff check src/
uv run mypy src/
```

**PyCharm setup:**

1. Open the project folder in PyCharm.
2. **Settings → Python Interpreter → Add → Existing** → point to `.venv/bin/python`.
3. (Optional) Add a `.env` plugin so PyCharm loads the vars for run configs.
