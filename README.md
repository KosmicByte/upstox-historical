# upstox-historical

> Fetch historical OHLCV candle data from the **Upstox v2 API** — clean DataFrames, CSV/Parquet output, full CLI.

---

## Features

- **Historical candles** — daily, weekly, monthly, 30-min, 1-min
- **Intraday candles** — today's live candles
- **Pydantic-validated** responses — no silent bad data
- **Rich CLI** with colour-coded table previews
- **CSV & Parquet** output
- **Pre-built instrument keys** for Nifty 50, BankNifty, FIN Nifty, large-cap equities
- **OAuth helper** — guided one-time login flow
- **pytest suite** included, zero real API calls needed

---

## Project structure

```
upstox-historical/
├── src/
│   └── upstox_historical/
│       ├── __init__.py
│       ├── auth.py          ← OAuth login helper
│       ├── cli.py           ← Typer CLI (upstox-fetch)
│       ├── client.py        ← httpx wrapper around Upstox v2 REST
│       ├── config.py        ← Settings (pydantic + .env)
│       ├── fetcher.py       ← DataFrame output, save to disk
│       ├── instruments.py   ← NSE instrument key constants
│       └── models.py        ← Pydantic response models
├── tests/
│   └── test_fetcher.py
├── examples/
│   └── basic_fetch.py
├── .env.example
├── .gitignore
├── pyproject.toml
└── README.md
```

---

## Quickstart

### 1 — Clone & create the venv with uv

```bash
git clone <your-repo>
cd upstox-historical

uv venv                        # creates .venv/
uv sync                        # installs all dependencies from pyproject.toml
uv sync --extra dev            # also installs pytest, ruff, mypy, ipykernel
```

### 2 — Get Upstox API credentials

1. Go to [https://developer.upstox.com/](https://developer.upstox.com/) and create an app.
2. Note your **API Key** and **API Secret**.
3. Set the redirect URI to `http://localhost:8000/callback` (or any URL you own).

### 3 — Set up your `.env`

```bash
cp .env.example .env
# edit .env and fill in UPSTOX_API_KEY and UPSTOX_API_SECRET
```

### 4 — Get your access token (one-time OAuth flow)

```bash
uv run python -m upstox_historical.auth
```

This opens a browser, logs you in, and prints your access token.  
Paste it into `.env`:

```env
UPSTOX_ACCESS_TOKEN=your_token_here
```

> **Note:** Upstox access tokens expire daily. You need to re-run this each day or automate token refresh.

---

## CLI usage

After installation, the `upstox-fetch` command is available:

```bash
# Fetch Nifty 50 daily OHLCV for all of 2024
uv run upstox-fetch fetch "NSE_INDEX|Nifty 50" \
    --interval day \
    --from 2024-01-01 \
    --to   2024-12-31 \
    --out-dir ./data

# Fetch BankNifty 30-minute candles
uv run upstox-fetch fetch "NSE_INDEX|Nifty Bank" \
    --interval 30minute \
    --from 2025-01-01 \
    --to   2025-03-31 \
    --format parquet

# Fetch today's intraday 1-minute candles
uv run upstox-fetch intraday "NSE_INDEX|Nifty 50"

# Run the OAuth login flow
uv run upstox-fetch login

# Print all built-in instrument keys
uv run upstox-fetch keys

# Help
uv run upstox-fetch --help
uv run upstox-fetch fetch --help
```

### Available intervals

| Flag value | Meaning         |
|------------|-----------------|
| `1minute`  | 1-minute candle |
| `30minute` | 30-minute candle|
| `day`      | Daily (default) |
| `week`     | Weekly          |
| `month`    | Monthly         |

---

## Python API

```python
from upstox_historical.fetcher import HistoricalFetcher
from upstox_historical.instruments import NSE
from upstox_historical.models import Interval

fetcher = HistoricalFetcher()   # reads .env automatically

# Fetch as DataFrame
df = fetcher.fetch(
    instrument_key=NSE.NIFTY_50,
    interval=Interval.D1,
    from_date="2024-01-01",
    to_date="2024-12-31",
)
print(df.tail())

# Fetch + save in one call
path = fetcher.fetch_and_save(
    instrument_key=NSE.BANKNIFTY,
    interval=Interval.I30M,
    from_date="2025-01-01",
    to_date="2025-03-31",
    out_dir="./data",
    fmt="parquet",
)
```

### Built-in instrument keys (`instruments.NSE`)

| Constant           | Instrument key                     |
|--------------------|------------------------------------|
| `NSE.NIFTY_50`     | `NSE_INDEX\|Nifty 50`              |
| `NSE.BANKNIFTY`    | `NSE_INDEX\|Nifty Bank`            |
| `NSE.FINNIFTY`     | `NSE_INDEX\|Nifty Fin Service`     |
| `NSE.INDIA_VIX`    | `NSE_INDEX\|India VIX`             |
| `NSE.RELIANCE`     | `NSE_EQ\|INE002A01018`             |
| `NSE.TCS`          | `NSE_EQ\|INE467B01029`             |
| `NSE.INFY`         | `NSE_EQ\|INE009A01021`             |
| … and more         |                                    |

---

## PyCharm setup

1. Open the project folder in PyCharm.
2. Go to **Settings → Python Interpreter → Add → Existing** and point to `.venv/bin/python`.
3. Run configurations are pre-loaded from `.idea/runConfigurations/upstox.xml`:
   - **fetch: Nifty 50 daily**
   - **intraday: BankNifty 1min**
   - **OAuth login**
   - **pytest**

---

## Running tests

```bash
uv run pytest tests/ -v
```

---

## Linting & type checking

```bash
uv run ruff check src/
uv run mypy src/
```

---

## Notes on Upstox rate limits

- The free Upstox API tier allows **~2 requests/second**.
- Historical data is available up to **2 years** back for intraday intervals.
- Daily/weekly/monthly data goes further back.
- Tokens expire **daily** — automate the OAuth flow or use the Upstox SDK if needed.

---

## License

MIT
