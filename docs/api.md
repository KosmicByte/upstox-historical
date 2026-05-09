# Python API

Use `upstox-historical` directly from your code instead of the CLI. Same engine, more control.

---

## Sync fetcher

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

---

## Async fetcher (recommended)

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

---

## Multi-instrument batch

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

---

## Incremental updates

```python
import asyncio
from upstox_historical.updater import plan_update, update

plan = plan_update("./data/NSE_EQ_INE002A01018_day_2020-01-01_2025-03-31.parquet")
print(f"Need to fetch {plan.new_from} to {plan.new_to}")

result = asyncio.run(update("./data/NSE_EQ_INE002A01018_day_2020-01-01_2025-03-31.parquet"))
print(f"Updated to {result.path}")
```

---

## Validation

```python
from upstox_historical.validation import validate, repair

report = validate(df)                # accepts any column casing
print(report.summary())

if report.errors:
    df = repair(df)                  # best-effort cleanup
```

`ValidationReport` exposes structured fields: `errors`, `warnings`, `ohlc_violations`, `zero_volume_rows`, `missing_trading_days`, `outlier_rows`, etc.

---

## Charting

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

## See also

- [CLI reference](./cli.md) — same operations from the command line
- [Design](./design.md) — what's happening under the hood (chunking, checkpoints, rate limiting)
