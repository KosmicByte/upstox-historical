# CLI reference

All commands are accessed via the `upstox-fetch` entry point. Run `upstox-fetch <command> --help` for complete options.

**Global flags:**
- `--verbose` / `-v` — enable DEBUG logging (re-enables per-URL httpx logs)
- `--version` — print version and exit

---

## `fetch` — historical candles

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

---

## `batch` — many instruments at once

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

---

## `update` — incremental refresh

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

---

## `intraday` — today's candles

```bash
# Today's 1-minute Nifty candles
uv run upstox-fetch intraday "NSE_INDEX|Nifty 50"

# Today's 5-minute BankNifty (resampled from 1-min)
uv run upstox-fetch intraday "NSE_INDEX|Nifty Bank" --interval 5minute
```

---

## `plot` — interactive charts

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

---

## `validate` — data quality

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

---

## `find` — interactive search

```bash
uv run upstox-fetch find             # prompts for query
uv run upstox-fetch find reliance    # prefills query, shows results
```

Arrow keys to navigate, Enter to select. The chosen `instrument_key` is copied to your clipboard automatically (if a clipboard backend is available).

Non-interactive mode prints results as a table:

```bash
uv run upstox-fetch find reliance --non-interactive
```

---

## `cache` — disk cache management

```bash
uv run upstox-fetch cache stats             # show cache contents + size
uv run upstox-fetch cache clear-bhav        # wipe Bhav Copy cache
uv run upstox-fetch cache clear-checkpoints # wipe fetch checkpoints
uv run upstox-fetch cache clear-all         # nuke everything
```

Cache location: `$UPSTOX_CACHE_DIR`, or `~/.cache/upstox-historical/` (Linux/Mac), or `%LOCALAPPDATA%/upstox-historical/cache/` (Windows).

---

## `login` / `keys`

```bash
uv run upstox-fetch login                  # OAuth flow → prints access token
uv run upstox-fetch keys                   # list built-in NSE instrument keys
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
