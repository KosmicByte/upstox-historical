# FAQ

**Why Upstox?**
Free multi-year historical candles through a stable API. Kite Connect charges for historical data; `yfinance` is an unstable scraper without NSE fields (deliverable volume, trade count).

**Is an Upstox account required?**
Yes, for API credentials. Funding is not required.

**Why Parquet by default?**
~5× smaller, ~10× faster reads, preserved dtypes. CSV: `--format csv`.

**Why lowercase in memory, capitalized on disk?**
Capitalized OHLCV is the standard financial-data schema. Lowercase internally matches Upstox API fields and preserves the v1.0 API contract (`df["close"]`).

**BSE support?**
Not yet. See [roadmap](./roadmap.md#mid-term).

**Why 20 requests/s?**
Sustained traffic above ~25 requests/s triggers 429s. 20 leaves headroom for retries and other Upstox calls. Override: `AsyncUpstoxClient(rate_limit=...)`.

**Bhav Copy usage**
Public NSE data. Cached after first fetch; live fetches are spaced 1 s apart. The delay must remain.

**Commercial use**
MIT-licensed. Compliance with Upstox API terms and NSE data-use policy rests with the user.

**Why asyncio over threads?**
Lower overhead and one shared rate-limit budget. The sync path remains for debugging.

**PyPI?**
Pending a stable API. Install from source.

**Are checkpoints portable across machines?**
The job hash is content-based, so copied checkpoints work; no sync mechanism exists. Treat the cache as machine-local.
