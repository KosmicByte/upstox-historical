# Design

## Output schema

| Mode | Columns |
|------|---------|
| Default | `Date, Open, High, Low, Close, Volume, VWAP, open_interest` |
| `--nse-enrich` | `Date, Symbol, Series, Prev Close, Open, High, Low, Close, Volume, VWAP, Turnover, Trades, Deliverable Volume, %Deliverble` |

## Column casing

| Stage | Casing |
|-------|--------|
| Processing (parsing, resampling, VWAP, API DataFrames) | lowercase, per Upstox API fields |
| Save (`fetcher._save`) | capitalized |
| Enrichment (`nse_enrichment._finalise_columns`) | capitalized before save |

v1.0 files are lowercase. Lowercase on read: `df.rename(columns=str.lower)`.

## Chunking

Ranges exceeding the per-request limit are split, fetched, and concatenated.

| Interval | Max per request |
|----------|-----------------|
| `1minute` | 1 month |
| `30minute` | 1 year |
| `day` | 1 year |
| `week` | 10 years |
| `month` | 10 years |

## Checkpoint resume

Each completed chunk is stored at `~/.cache/upstox-historical/checkpoints/<job-hash>/chunk_NNNN.parquet`. Job hash: SHA-256 of `(instrument_key, interval, from_date, to_date)`.

On rerun with identical arguments:
1. Completed chunks load from disk.
2. Missing chunks are fetched.
3. The checkpoint directory is deleted on success.

## Bhav Copy cache

Parsed files: `~/.cache/upstox-historical/bhav/YYYY-MM-DD.parquet`. Non-trading days (NSE 404) get a zero-byte `.miss` marker.

## Rate limiting and retries

| Layer | Setting |
|-------|---------|
| `aiolimiter.AsyncLimiter(20, 1.0)` | 20 requests/s, global |
| `asyncio.Semaphore(5)` | 5 in-flight requests |
| `tenacity` | 5 attempts, backoff 1 → 2 → 4 → 8 s, cap 30 s |

Retried: 5xx, 429, timeouts, connection errors. Not retried: 401.

## NSE Bhav Copy enrichment

| Column | Source |
|--------|--------|
| Symbol | `--symbol` or instrument key |
| Series | `--series` or key prefix (`NSE_EQ` → `EQ`) |
| Prev Close | `PREV_CLOSE` |
| Turnover | `VWAP × Volume` (computed) |
| Trades | `NO_OF_TRADES` |
| Deliverable Volume | `DELIV_QTY` |
| %Deliverble | `DELIV_PER` |

Intraday rows inherit the day's values. `timestamp` is renamed to `Date`.

## Project structure

```
upstox-historical/
├── src/upstox_historical/
│   ├── __init__.py          Exports, version
│   ├── auth.py              OAuth login
│   ├── cli.py               Typer CLI
│   ├── config.py            Settings, logging
│   ├── instruments.py       Instrument key constants
│   ├── models.py            Pydantic response models
│   ├── client.py            Sync HTTP client
│   ├── async_client.py    ⦿ Async HTTP client
│   ├── fetcher.py           Sync fetcher
│   ├── async_fetcher.py   ⦿ Async fetcher
│   ├── nse_enrichment.py    Bhav Copy enrichment
│   ├── cache.py           ⦿ Disk cache
│   ├── updater.py         ⦿ Incremental update
│   ├── validation.py      ⦿ Quality checks
│   ├── plotting.py        ⦿ Plotly charts
│   └── search.py          ⦿ Instrument search
├── tests/test_fetcher.py
├── examples/basic_fetch.py
├── .env.example
├── pyproject.toml
├── README.md
└── uv.lock
```

`⦿` New in v1.1.0.

## Development

```bash
uv run pytest tests/ -v
uv run ruff check src/
uv run mypy src/
```
