# Python API

## Sync fetcher

`fetch()` returns lowercase OHLCV columns. Columns are capitalized on save only.

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
)                                    # lowercase columns

path = fetcher.fetch_and_save(
    instrument_key=NSE.NIFTY_50,
    interval=Interval.D1,
    from_date="2024-01-01",
    to_date="2024-12-31",
    fmt="parquet",
)                                    # capitalized columns
```

## Async fetcher

Concurrent and resumable. Recommended for multi-chunk ranges.

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

asyncio.run(main())
```

## Batch

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
    )                                # dict: instrument_key -> DataFrame

asyncio.run(main())
```

## Incremental update

```python
import asyncio
from upstox_historical.updater import plan_update, update

path = "./data/NSE_EQ_INE002A01018_day_2020-01-01_2025-03-31.parquet"

plan = plan_update(path)             # plan.new_from, plan.new_to
result = asyncio.run(update(path))   # result.path
```

## Validation

```python
from upstox_historical.validation import validate, repair

report = validate(df)                # any column casing
print(report.summary())

if report.errors:
    df = repair(df)                  # best-effort
```

`ValidationReport` fields: `errors`, `warnings`, `ohlc_violations`, `zero_volume_rows`, `missing_trading_days`, `outlier_rows`.

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
