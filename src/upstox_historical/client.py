"""
client.py — thin httpx wrapper around the Upstox v2 REST API.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from upstox_historical.config import Settings, get_settings

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30.0  # seconds


class UpstoxAuthError(Exception):
    """Raised when the access token is missing or rejected by Upstox."""


class UpstoxAPIError(Exception):
    """Raised for non-2xx HTTP responses or unexpected API errors."""

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        super().__init__(f"HTTP {status_code}: {message}")


class UpstoxClient:
    """
    Synchronous HTTP client for the Upstox v2 API.

    Usage::

        from upstox_historical.client import UpstoxClient

        client = UpstoxClient()                  # reads token from .env
        client = UpstoxClient(access_token="…")  # explicit token
    """

    def __init__(
        self,
        access_token: str | None = None,
        settings: Settings | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self._settings = settings or get_settings()
        self._token = access_token or self._settings.access_token

        if not self._token:
            raise UpstoxAuthError(
                "No access token found. Set UPSTOX_ACCESS_TOKEN in your .env file "
                "or pass access_token= explicitly. See README for OAuth flow."
            )

        self._base_url = self._settings.base_url
        self._http = httpx.Client(
            base_url=self._base_url,
            headers=self._build_headers(),
            timeout=timeout,
        )
        logger.debug("UpstoxClient initialised (base_url=%s)", self._base_url)

    # ── private ──────────────────────────────────────────

    def _build_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
            "Api-Version": "2.0",
        }

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        logger.debug("GET %s  params=%s", path, params)
        response = self._http.get(path, params=params)
        self._raise_for_status(response)
        return response.json()  # type: ignore[return-value]

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.status_code == 401:
            raise UpstoxAuthError(
                "401 Unauthorised — your access token is invalid or expired. "
                "Re-run the OAuth flow (see README)."
            )
        if response.status_code == 429:
            raise UpstoxAPIError(429, "Rate limit exceeded. Please wait before retrying.")
        if response.is_error:
            try:
                msg = response.json().get("errors", [{}])[0].get("message", response.text)
            except Exception:
                msg = response.text
            raise UpstoxAPIError(response.status_code, msg)

    # ── public API ────────────────────────────────────────

    def get_historical_candles(
        self,
        instrument_key: str,
        interval: str,
        from_date: str,
        to_date: str,
    ) -> dict[str, Any]:
        """
        Fetch historical OHLCV candles.

        Parameters
        ----------
        instrument_key : str
            e.g. ``"NSE_INDEX|Nifty 50"`` or ``"NSE_EQ|INE009A01021"``
        interval : str
            One of ``1minute``, ``30minute``, ``day``, ``week``, ``month``
        from_date : str
            Start date ``YYYY-MM-DD``
        to_date : str
            End date   ``YYYY-MM-DD``
        """
        path = f"/historical-candle/{instrument_key}/{interval}/{to_date}/{from_date}"
        return self._get(path)

    def get_intraday_candles(
        self,
        instrument_key: str,
        interval: str,
    ) -> dict[str, Any]:
        """
        Fetch today's intraday OHLCV candles (no date range needed).

        Parameters
        ----------
        instrument_key : str
            e.g. ``"NSE_INDEX|Nifty 50"``
        interval : str
            ``1minute`` or ``30minute``
        """
        path = f"/historical-candle/intraday/{instrument_key}/{interval}"
        return self._get(path)

    def search_instruments(self, query: str) -> dict[str, Any]:
        """Search for instruments by name / trading symbol."""
        return self._get("/instruments/search", params={"q": query})

    def get_market_quote(self, instrument_keys: list[str]) -> dict[str, Any]:
        """Fetch live LTP quote for one or more instruments."""
        return self._get(
            "/market-quote/ltp",
            params={"instrument_key": ",".join(instrument_keys)},
        )

    def close(self) -> None:
        self._http.close()

    # ── context manager ───────────────────────────────────

    def __enter__(self) -> "UpstoxClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
