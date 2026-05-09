# Troubleshooting

## Common errors

**`401 Unauthorised` on first request.** Your access token expired. Run `upstox-fetch login` to get a fresh one.

**`429 Rate limit exceeded` warnings in logs.** The retry layer is handling it. If you see it constantly, lower `rate_limit` in `AsyncUpstoxClient(...)` from 20 to 10.

**`Missing OHLC columns: ['close', 'high', 'low', 'open']`.** You're running an older `validation.py` that expects lowercase. Upgrade to v1.1.0 — validation is now case-insensitive.

**Fetch hangs or runs slowly.** Set `--verbose` to see per-chunk progress. If it's stuck at "Fetching", it's likely rate-limit backoff — wait 30 seconds.

**Bhav Copy 404 for recent dates.** NSE publishes the Bhav Copy ~1 hour after market close. Dates before today-1 should always be available.

**Plotly chart shows no candles.** Run `upstox-fetch validate` on the file first — the file may be empty or malformed.

**Update rewrites with a different filename.** By design — the filename encodes the date range, so extending the range changes the name. Use `rename_output=False` in the Python API for stable filenames.

**Where are my cached files?** `upstox-fetch cache stats` prints the location.

---

## Rate limits and caveats

- **Historical candles:** Upstox allows bursty short-term traffic but aggressive sustained load gets 429. The built-in rate limiter (20 req/s) is conservative; raise it if your tier allows more.
- **Access tokens** expire **daily** at ~3:30 AM IST. Automate `upstox-fetch login` or re-run each morning.
- **1-minute data** is available for the **last ~2 years**. Longer ranges return partial results.
- **Daily/weekly/monthly data** goes much further back (varies by instrument).
- **NSE Bhav Copy rate:** NSE's archive is bot-sensitive. The client adds a 1-second delay between live Bhav Copy fetches; once cached, it's instant.
