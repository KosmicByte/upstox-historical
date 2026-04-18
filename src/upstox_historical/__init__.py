"""
upstox_historical
~~~~~~~~~~~~~~~~~
Fetch historical OHLCV data from the Upstox API v2.
"""

__version__ = "0.1.0"
__all__ = ["UpstoxClient", "HistoricalFetcher"]

from upstox_historical.client import UpstoxClient
from upstox_historical.fetcher import HistoricalFetcher
