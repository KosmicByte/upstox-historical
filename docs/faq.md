# FAQ

Design choices and recurring questions. For runtime errors and rate-limit issues, see [troubleshooting.md](./troubleshooting.md) instead.

---

## Why Upstox and not Zerodha Kite / Yahoo Finance / yfinance?

Upstox's v2 API exposes long-range historical candles (multi-year 1-minute data, decade-plus daily data) for free with a personal account, behind a stable interface. Kite Connect requires a paid subscription for historical data, and `yfinance` is a scraper that breaks regularly and gives you zero NSE-specific fields like deliverable volume or trade count. Upstox hits the right point on the cost/quality/stability triangle for serious Indian-equities research.

## Can I use this without an Upstox account?

No. The fetcher needs API credentials, and Upstox issues those only to account holders. The account itself is free; you don't need to fund it or trade.

## Why does my access token expire every day?

That's an Upstox policy, not something this library can change. Tokens issued by Upstox expire at ~3:30 AM IST regardless of activity. Run `upstox-fetch login` each morning, or wrap it in a cron job.

## Why Parquet by default instead of CSV?

Parquet is roughly 5× smaller on disk, ~10× faster to read with pandas/polars/duckdb, and preserves dtypes (datetime, int64, float64) without parser ambiguity. CSV is supported with `--format csv` for interop with tools that don't speak Parquet.

## Why capitalize OHLCV columns on save?

`Date, Open, High, Low, Close, Volume` is the conventional schema in financial data — it matches what most charting libraries, broker exports, and academic datasets use. Upstox's API uses lowercase internally, so the package keeps lowercase during processing and renames at save time only. The rename is one line if you want lowercase: `df.rename(columns=str.lower)`.

## Why is the in-memory DataFrame lowercase but the saved file capitalized?

Two reasons. First, lowercase matches the Upstox API field names, which means the parsing code stays simple. Second, capitalizing only at the I/O boundary means the V1.0 Python API contract is preserved exactly — every old script that reads `df["close"]` keeps working. The capitalization is a *file-format* convention, not an internal one.

## Does this support BSE?

Not yet. Everything assumes NSE today (Bhav Copy parser, instrument keys, trading calendar). BSE support is on the [roadmap](./roadmap.md#mid-term).

## How far back can I get 1-minute data?

Upstox keeps roughly 2 years of 1-minute history. Older requests return partial results with no warning. Daily/weekly/monthly bars go back much further (10+ years on most instruments).

## Why is the rate limit set to 20 req/sec? Can I raise it?

Upstox's documented limit is higher, but real-world traffic shows 429s creeping in past ~25 sustained req/sec. Twenty is conservative on purpose — it leaves headroom for the retry layer and for any other Upstox calls your code might make. If your tier allows more and you're confident, pass `rate_limit=...` to `AsyncUpstoxClient`.

## Is the NSE Bhav Copy enrichment legal/safe?

Bhav Copies are public NSE data, freely downloadable from their archive. The package caches them on first fetch to avoid hammering NSE's servers, and adds a polite 1-second delay between live fetches. Don't disable that delay.

## Can I use this commercially?

The library is MIT-licensed, so yes from a license standpoint. **You** are responsible for complying with Upstox's API terms of service and NSE's data-use policy for any commercial application. Read both before deploying anything user-facing.

## Why async over plain threads?

For I/O-bound work like API fetching, asyncio with a rate limiter and semaphore is significantly more efficient than a thread pool — fewer context switches, less memory, and crucially, *one* shared rate limit budget instead of N threads each trying to back off independently. The sync path is kept for debugging and for callers who don't want to deal with `asyncio.run`.

## Why isn't this on PyPI?

It will be once the API is stable enough to commit to semantic versioning meaningfully. Right now `git clone + uv sync` is two extra commands and lets the project iterate fast without breaking installed users.

## Is checkpoint resume safe across machines?

Within a machine, yes. Across machines, technically yes if you copy `~/.cache/upstox-historical/checkpoints/` over — the job hash is content-based, not machine-specific — but there's no built-in mechanism for that. Treat the cache as machine-local.


