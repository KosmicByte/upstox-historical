# Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `401 Unauthorised` | Access token expired | `upstox-fetch login` |
| Frequent `429` | Sustained rate too high | `AsyncUpstoxClient(rate_limit=10)` |
| `Missing OHLC columns: [...]` | Pre-v1.1 `validation.py` | Upgrade to v1.1.0 |
| Fetch stalls | Rate-limit backoff | Wait ~30 s; `--verbose` shows progress |
| Bhav Copy 404 for current day | Published ~1 h after close | Retry later |
| Empty chart | Empty or malformed file | `upstox-fetch validate` |
| `update` changes filename | Filename encodes date range | `rename_output=False` (Python API) |

## Limits

- Access tokens expire daily at ~03:30 IST.
- `1minute` history: ~2 years. Older ranges return partial data without warning.
- `day`, `week`, `month` history: 10+ years on most instruments.
- Default rate limit: 20 requests/s.
- Live Bhav Copy fetches: 1 s apart.
- Cache location: `upstox-fetch cache stats`.
