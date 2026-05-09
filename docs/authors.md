# Authors

## Maintainer

**Aditya Mishra**
GitHub: [@KosmicByte](https://github.com/KosmicByte)

Built and maintained as part of a broader stack for quantitative research and trading on Indian equities.

---

## Built on the shoulders of

`upstox-historical` would be a much larger project without the work of the maintainers behind these libraries:

- **[httpx](https://www.python-httpx.org/)** — sync and async HTTP client; the foundation of every API call.
- **[aiolimiter](https://github.com/mjpieters/aiolimiter)** — leaky-bucket rate limiting for asyncio.
- **[tenacity](https://tenacity.readthedocs.io/)** — composable retry logic with exponential backoff.
- **[pandas](https://pandas.pydata.org/)** & **[pyarrow](https://arrow.apache.org/docs/python/)** — DataFrame ergonomics and the Parquet pipeline.
- **[Plotly](https://plotly.com/python/)** — interactive candlestick charts that work in any browser.
- **[Typer](https://typer.tiangolo.com/)** — the CLI framework powering `upstox-fetch`.
- **[Rich](https://rich.readthedocs.io/)** — progress bars and colored terminal output.
- **[questionary](https://github.com/tmbo/questionary)** — interactive instrument search.
- **[uv](https://github.com/astral-sh/uv)** — fast, reproducible dependency management.

Data sources:
- **[Upstox v2 API](https://upstox.com/developer/api-documentation/)** — historical OHLCV candles.
- **[NSE Bhav Copy archive](https://www.nseindia.com/all-reports)** — daily delivery and trade summaries.

---
