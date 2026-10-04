# Roadmap

Scope: validated historical OHLCV for Indian equities and supporting data tooling. Priorities may change.

## v1.2.x

- **Corporate actions** — `--corp-actions` on `fetch` and `update`, from NSE announcements (bonuses, splits, rights).
- **Adjusted close** — `Adj Close` column from corporate-action data.
- **Instrument keys** — full Nifty 200 and curated mid/small caps in `instruments.NSE`.
- **Resampling-aware validation** — no gap flags on legitimate empty bars in resampled intraday data.

## Mid-term

- **F&O Bhav Copy** — `--fno-enrich`; separate schema and cache, shared async and checkpoint core.
- **BSE** — Bhav Copy enrichment and instrument keys on the shared fetcher core.
- **Async-first refactor** — `HistoricalFetcher` as a wrapper over `AsyncHistoricalFetcher`; removes ~300 lines of duplicate logic.
- **Cache versioning** — version marker for clean invalidation on schema change.

## Speculative

- WebSocket tick streaming (likely a separate repo)
- DuckDB output
- Pluggable enrichers (alt-data, fundamentals, sentiment)
- Bundled NSE trading calendar for offline validation

## Out of scope

- Trading, order management, portfolio construction
- Backtesting frameworks
- Hosted datasets
