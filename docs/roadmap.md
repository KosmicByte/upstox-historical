# Roadmap

A rough plan of where `upstox-historical` is headed. Priorities shift; nothing here is a promise.

The repo's scope is **clean, validated historical OHLCV for Indian equities, plus the data-engineering boilerplate around it.** Anything outside that scope is intentionally pushed elsewhere (see [Out of scope](#out-of-scope)).

---

## Near-term (v1.2.x)

**Corporate actions enrichment.** Bonuses, splits, and rights affect raw OHLCV in ways that quietly break long-horizon backtests. Plan: add an optional `--corp-actions` flag to `fetch` and `update` that reads NSE corporate-action announcements and emits adjusted-close columns alongside the raw close.

**Adjusted-close column.** A precomputed `Adj Close` column derived from the corporate-action data above, so downstream models don't have to rebuild the adjustment math each time.

**More built-in instrument keys.** Expand `instruments.NSE` to cover the full Nifty 200 and a curated set of mid/small-cap names. Tracked separately so the constant list stays useful without ballooning into thousands.

**Resampling-aware validation.** `validate` currently treats resampled intraday gaps the same as native gaps. Make the gap detector aware of the source interval so it stops flagging legitimate empty bars in resampled output.

---

## Mid-term

**NSE F&O Bhav Copy support.** Daily summaries for futures and options. Different schema, different cache key, but the same async + checkpoint scaffolding. Likely under a separate `--fno-enrich` flag to keep the equity path uncluttered.

**BSE support.** Right now everything assumes NSE. Add a parallel enrichment path for BSE Bhav Copy and instrument keys; share as much of the fetcher core as possible.

**Async-first refactor.** Sync `HistoricalFetcher` stays for backwards compatibility but graduates to a thin wrapper around `AsyncHistoricalFetcher`. Removes ~300 lines of duplicated chunking/checkpoint logic.

**Cache-format versioning.** Add a `version` marker to the on-disk cache so future schema changes can invalidate stale entries cleanly instead of failing at parse time.

---

## Speculative

Things that are interesting but unscheduled:

- **Streaming tick ingestion** via Upstox WebSocket — would change the project's character significantly; probably belongs in a sibling repo if it happens at all.
- **DuckDB output** as an alternative to CSV/Parquet, for users who want SQL access over a multi-instrument dataset without a separate ETL step.
- **Pluggable enrichment** — a small protocol so users can register their own enrichers (alt-data, fundamentals, sentiment) without forking.
- **Pre-built calendar** — ship the NSE trading calendar as a JSON/parquet asset rather than reconstructing it from Bhav Copy hits, so validation works offline.

---

## Out of scope

To keep the project sharp:

- **Trading, order management, portfolio construction.** Those live downstream in separate engines.
- **Backtesting frameworks.** Many already exist; this repo feeds them, doesn't compete with them.
- **Data hosting.** No managed datasets, no API behind a hosted dataset. Run it yourself, own your cache.
