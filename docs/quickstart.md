# Quick start

A 5-minute path from a fresh clone to a verified, charted dataset.

## 1 — Clone and install

```bash
git clone git@github.com:KosmicByte/upstox-historical.git
cd upstox-historical

uv venv                              # creates .venv/
uv sync                              # installs all deps from pyproject.toml
uv sync --extra dev                  # also installs pytest, ruff, mypy, ipykernel
```

## 2 — Upstox credentials

1. Go to [https://account.upstox.com/developer/apps](https://account.upstox.com/developer/apps) and create an app.
2. Note your **API Key** and **API Secret**.
3. Set the redirect URI to `http://localhost:8000/callback` (or any URL you own).
4. Copy the template `.env`:

   ```bash
   cp .env.example .env
   # edit .env and fill in UPSTOX_API_KEY and UPSTOX_API_SECRET
   ```

## 3 — Login for access token

```bash
uv run upstox-fetch login
```

This opens a browser, logs you in to Upstox, and prints your access token. Paste it into `.env`:

```env
UPSTOX_ACCESS_TOKEN=your_token_here
```

> **Note:** Upstox access tokens expire daily at ~3:30 AM IST. Re-run `upstox-fetch login` each morning to refresh.

## 4 — First fetch

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

## Where to next

- [CLI reference](./cli.md) — every command and flag
- [Python API](./api.md) — using `upstox-historical` from your own code
- [Design](./design.md) — output schema and how things work under the hood
- [Troubleshooting](./troubleshooting.md) — when something doesn't behave
