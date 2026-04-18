"""
upstox_historical
~~~~~~~~~~~~~~~~~
Fetch historical OHLCV data from the Upstox API v2.

V1.1 additions:
- AsyncHistoricalFetcher: concurrent chunked fetching with checkpoint resume.
- Disk-cached NSE Bhav Copy enrichment.
- Tenacity-powered retries on transient HTTP errors.
- Data validation + outlier detection.
- Incremental updater for existing saved files.
- Multi-instrument batch fetch.
- Plotly-based charting with technical indicators.
- Interactive instrument search.
"""

__version__ = "0.2.0"
__all__ = [
    "UpstoxClient",
    "HistoricalFetcher",
    "AsyncUpstoxClient",
    "AsyncHistoricalFetcher",
]

from upstox_historical.async_client import AsyncUpstoxClient
from upstox_historical.async_fetcher import AsyncHistoricalFetcher
from upstox_historical.client import UpstoxClient
from upstox_historical.fetcher import HistoricalFetcher
