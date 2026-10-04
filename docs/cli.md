# CLI reference

Entry point: `upstox-fetch`. Full options: `upstox-fetch <command> --help`.

| Global flag | Description |
|-------------|-------------|
| `--verbose`, `-v` | DEBUG logging, including per-URL httpx logs |
| `--version` | Print version |

---

## fetch

OHLCV candles for one instrument. Async by default.

```bash
uv run upstox-fetch fetch "NSE_INDEX|Nifty 50" \
    --interval day --from 2020-01-01 --to 2025-10-31 \
    --format parquet --out-dir ./data
```

| Flag | Default | Description |
|------|---------|-------------|
| `--interval`, `-i` | `day` | Candle interval |
| `--from`, `-f` | `2024-01-01` | Start date (YYYY-MM-DD) |
| `--to`, `-t` | `2024-12-31` | End date (YYYY-MM-DD) |
| `--out-dir`, `-o` | `./data` | Output directory |
| `--format` | `parquet` | `csv` or `parquet` |
| `--sync` | off | Sync fetcher |
| `--nse-enrich` | off | Add NSE Bhav Copy columns |
| `--symbol`, `-s` | auto | NSE ticker for enrichment |
| `--series` | auto | NSE series for enrichment |
| `--preview/--no-preview` | on | Print first 10 rows |
| `--validate/--no-validate` | on | Run quality checks |

```bash
uv run upstox-fetch fetch "NSE_EQ|INE002A01018" \
    -i day -f 2024-01-01 -t 2024-12-31 \
    --nse-enrich --symbol RELIANCE --series EQ --format parquet
```

ISIN-keyed instruments require `--symbol`: Bhav Copy is keyed by ticker, not ISIN.

## batch

Concurrent fetch under one shared rate limit. Built-in keys resolve automatically; other tickers map to `NSE_EQ|<TICKER>`. Prints row count and validation status per instrument.

```bash
uv run upstox-fetch batch \
    --symbols RELIANCE,TCS,INFY,HDFCBANK,ICICIBANK \
    --interval day --from 2024-01-01 --to 2024-12-31 \
    --nse-enrich --format parquet
```

## update

Fetches from the file's last timestamp to today (or `--until`) and writes in place. Enrichment is detected from existing columns. The file is renamed to the new date range.

```bash
uv run upstox-fetch update ./data/NSE_EQ_INE002A01018_day_2020-01-01_2025-10-31.parquet
uv run upstox-fetch update ./data/<file>.parquet --dry-run
uv run upstox-fetch update ./data/<file>.parquet --until 2025-12-31
```

## intraday

Current-day candles.

```bash
uv run upstox-fetch intraday "NSE_INDEX|Nifty 50"
uv run upstox-fetch intraday "NSE_INDEX|Nifty Bank" --interval 5minute
```

## plot

Standalone HTML: candlesticks, volume, indicators. Output: `<filename>.html` beside the source.

```bash
uv run upstox-fetch plot ./data/reliance.parquet \
    --indicators sma20,sma50,bb20,rsi14,macd,vwap_overlay --open
```

| Indicator | Description |
|-----------|-------------|
| `sma20`, `sma50`, `sma200` | Simple moving average |
| `ema12`, `ema26` | Exponential moving average |
| `bb20` | Bollinger Bands (SMA ± 2σ) |
| `rsi14` | RSI (subplot) |
| `macd` | MACD (12, 26, 9) with signal and histogram (subplot) |
| `vwap_overlay` | `VWAP` column overlay |

## validate

```bash
uv run upstox-fetch validate ./data/reliance.parquet
```

Checks:
- OHLC ordering (`high ≥ open`, `close ≥ low ≥ 0`)
- Negative and zero volume
- Duplicate and non-monotonic timestamps
- Missing trading days (against Bhav Copy cache)
- Log-return outliers (z-score > 8σ)

Errors indicate bad data; warnings indicate possible anomalies. No findings: `✓ clean`.

## find

Interactive instrument search. Selection copies the `instrument_key` to the clipboard, if a backend is available.

```bash
uv run upstox-fetch find
uv run upstox-fetch find reliance
uv run upstox-fetch find reliance --non-interactive    # table output
```

## cache

```bash
uv run upstox-fetch cache stats
uv run upstox-fetch cache clear-bhav
uv run upstox-fetch cache clear-checkpoints
uv run upstox-fetch cache clear-all
```

Location: `$UPSTOX_CACHE_DIR`; default `~/.cache/upstox-historical/` (Linux, macOS) or `%LOCALAPPDATA%/upstox-historical/cache/` (Windows).

## login, keys

```bash
uv run upstox-fetch login    # OAuth; prints access token
uv run upstox-fetch keys     # built-in instrument keys
```

---

## Intervals

| Interval | Source |
|----------|--------|
| `1minute` | native |
| `2minute`, `3minute`, `5minute`, `10minute`, `15minute`, `20minute`, `25minute` | resampled from `1minute` |
| `30minute` | native |
| `day` | native |
| `week` | native |
| `month` | native |

## Built-in instrument keys

`upstox_historical.instruments.NSE`:

| Constant | Key |
|----------|-----|
| `NIFTY_50` | `NSE_INDEX\|Nifty 50` |
| `BANKNIFTY` | `NSE_INDEX\|Nifty Bank` |
| `FINNIFTY` | `NSE_INDEX\|Nifty Fin Service` |
| `INDIA_VIX` | `NSE_INDEX\|India VIX` |
| `RELIANCE` | `NSE_EQ\|INE002A01018` |
| `TCS` | `NSE_EQ\|INE467B01029` |
| `INFY` | `NSE_EQ\|INE009A01021` |

Full list: `upstox-fetch keys`.
