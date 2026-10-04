# Quick start

## 1. Install

```bash
git clone git@github.com:KosmicByte/upstox-historical.git
cd upstox-historical
uv venv
uv sync
uv sync --extra dev    # optional: pytest, ruff, mypy, ipykernel
```

## 2. Credentials

1. Create an app at [account.upstox.com/developer/apps](https://account.upstox.com/developer/apps).
2. Set the redirect URI to `http://localhost:8000/callback`.
3. Add the API key and secret to `.env`:

```bash
cp .env.example .env    # set UPSTOX_API_KEY, UPSTOX_API_SECRET
```

## 3. Access token

```bash
uv run upstox-fetch login
```

Add the printed token to `.env`:

```env
UPSTOX_ACCESS_TOKEN=<token>
```

Tokens expire daily at ~03:30 IST.

## 4. Fetch, validate, plot

```bash
uv run upstox-fetch fetch "NSE_INDEX|Nifty 50" \
    --interval day --from 2024-01-01 --to 2024-12-31 --format parquet

uv run upstox-fetch validate ./data/NSE_INDEX_Nifty_50_day_2024-01-01_2024-12-31.parquet

uv run upstox-fetch plot ./data/NSE_INDEX_Nifty_50_day_2024-01-01_2024-12-31.parquet \
    --indicators sma20,sma50,sma200,rsi14 --open
```
