"""
client.py — httpx wrapper around the Upstox v2 and v3 REST APIs.

Two clients are provided:

  UpstoxClient   — v2 REST (historical candles, intraday).
                   Uses access_token or analytics_token.

  UpstoxClientV3 — v3 REST (LTP quotes, OHLC quotes, market status,
                   WebSocket authorize URL). Prefers analytics_token.
                   This is what stream.py and market_info.py use.

Token priority (both clients)
------------------------------
analytics_token is preferred when available (1-year validity).
Falls back to access_token (daily OAuth) automatically via best_token().
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from upstox_historical.config import Settings, get_settings

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30.0  # seconds


# ── Exceptions ────────────────────────────────────────────────────────

class UpstoxAuthError(Exception):
    """Raised when the token is missing or rejected by Upstox."""


class UpstoxAPIError(Exception):
    """Raised for non-2xx HTTP responses or unexpected API errors."""

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        super().__init__(f"HTTP {status_code}: {message}")


# ── Shared helpers ────────────────────────────────────────────────────

def _raise_for_status(response: httpx.Response) -> None:
    if response.status_code == 401:
        raise UpstoxAuthError(
            "401 Unauthorised — token is invalid or expired. "
            "For market data APIs use UPSTOX_ANALYTICS_TOKEN (1-year). "
            "For order/portfolio APIs use UPSTOX_ACCESS_TOKEN (daily OAuth)."
        )
    if response.status_code == 429:
        raise UpstoxAPIError(429, "Rate limit exceeded. Slow down requests.")
    if response.is_error:
        try:
            msg = response.json().get("errors", [{}])[0].get("message", response.text)
        except Exception:
            msg = response.text
        raise UpstoxAPIError(response.status_code, msg)


# ── v2 Client ─────────────────────────────────────────────────────────

class UpstoxClient:
    """
    Synchronous HTTP client for the Upstox **v2** API.

    Handles historical candles and intraday candles.
    Prefers analytics_token when available; falls back to access_token.

    Usage::

        client = UpstoxClient()                   # reads token from .env
        client = UpstoxClient(token="<token>")    # explicit token
    """

    def __init__(
        self,
        token: str | None = None,
        # kept for backward compat — old callers passed access_token=
        access_token: str | None = None,
        settings: Settings | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self._settings = settings or get_settings()
        self._token = token or access_token or self._settings.best_token()

        self._http = httpx.Client(
            base_url=self._settings.base_url,
            headers=self._build_headers(),
            timeout=timeout,
        )
        logger.debug(
            "UpstoxClient (v2) initialised | analytics=%s",
            self._settings.has_analytics_token(),
        )

    def _build_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
            "Api-Version": "2.0",
        }

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        logger.debug("GET %s  params=%s", path, params)
        response = self._http.get(path, params=params)
        _raise_for_status(response)
        return response.json()  # type: ignore[return-value]

    # ── Historical data ───────────────────────────────────────────────

    def get_historical_candles(
        self,
        instrument_key: str,
        interval: str,
        from_date: str,
        to_date: str,
    ) -> dict[str, Any]:
        """
        Fetch historical OHLCV candles (v2).

        Parameters
        ----------
        instrument_key : str
            e.g. ``"NSE_INDEX|Nifty 50"``
        interval : str
            ``1minute`` | ``30minute`` | ``day`` | ``week`` | ``month``
        from_date, to_date : str
            ``YYYY-MM-DD``
        """
        path = f"/historical-candle/{instrument_key}/{interval}/{to_date}/{from_date}"
        return self._get(path)

    def get_intraday_candles(
        self,
        instrument_key: str,
        interval: str,
    ) -> dict[str, Any]:
        """Fetch today's intraday OHLCV candles (v2, no date range needed)."""
        path = f"/historical-candle/intraday/{instrument_key}/{interval}"
        return self._get(path)

    def search_instruments(self, query: str) -> dict[str, Any]:
        """Search instruments by name / trading symbol."""
        return self._get("/instruments/search", params={"q": query})

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "UpstoxClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


# ── v3 Client ─────────────────────────────────────────────────────────

class UpstoxClientV3:
    """
    Synchronous HTTP client for the Upstox **v3** API.

    Covers:
      - LTP quotes (up to 500 instruments per call)
      - OHLC quotes (up to 500 instruments per call)
      - Full market quotes
      - Market information / exchange status
      - WebSocket authorize URL (required before opening a live stream)

    All endpoints here work with the Analytics Token (1-year).
    No daily OAuth renewal needed for any of these.

    Usage::

        client = UpstoxClientV3()                 # reads token from .env
        client = UpstoxClientV3(token="<token>")  # explicit token
    """

    def __init__(
        self,
        token: str | None = None,
        settings: Settings | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self._settings = settings or get_settings()
        self._token = token or self._settings.best_token()

        self._http = httpx.Client(
            base_url=self._settings.base_url_v3,
            headers=self._build_headers(),
            timeout=timeout,
        )
        logger.debug(
            "UpstoxClientV3 initialised | analytics=%s",
            self._settings.has_analytics_token(),
        )

    def _build_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
            "Api-Version": "2.0",
        }

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        logger.debug("GET v3 %s  params=%s", path, params)
        response = self._http.get(path, params=params)
        _raise_for_status(response)
        return response.json()  # type: ignore[return-value]

    # ── Market quotes ─────────────────────────────────────────────────

    def get_ltp(self, instrument_keys: list[str]) -> dict[str, Any]:
        """
        Fetch Last Traded Price for up to 500 instruments (v3).

        Returns raw API response. Use market_info.get_ltp() for a clean scalar.

        Parameters
        ----------
        instrument_keys : list[str]
            e.g. ``["NSE_INDEX|Nifty 50", "NSE_EQ|INE002A01018"]``
        """
        return self._get(
            "/market-quote/ltp",
            params={"instrument_key": ",".join(instrument_keys)},
        )

    def get_ohlc(self, instrument_keys: list[str], interval: str = "1d") -> dict[str, Any]:
        """
        Fetch OHLC quotes for up to 500 instruments (v3).

        Parameters
        ----------
        instrument_keys : list[str]
        interval : str
            ``1d`` (default) | ``1week`` | ``1month``
        """
        return self._get(
            "/market-quote/ohlc",
            params={
                "instrument_key": ",".join(instrument_keys),
                "interval": interval,
            },
        )

    def get_full_quote(self, instrument_keys: list[str]) -> dict[str, Any]:
        """
        Fetch full market quotes (OHLC + bid/ask depth + volume + OI) for
        up to 500 instruments (v3).
        """
        return self._get(
            "/market-quote/quotes",
            params={"instrument_key": ",".join(instrument_keys)},
        )

    # ── Market information ────────────────────────────────────────────

    def get_market_status(self, exchange: str = "NSE") -> dict[str, Any]:
        """
        Fetch the current trading status for a single exchange.

        Uses the v2 endpoint ``/v2/market/status/{exchange}`` — there is no v3
        variant. Called via an absolute URL because this client's base_url is v3.

        Parameters
        ----------
        exchange : str
            Exchange code: NSE | BSE | NFO | MCX | CDS | BFO | BCD. Default "NSE".

        Returns
        -------
        dict
            API response, e.g.::

                {"status": "success",
                 "data": {"exchange": "NSE",
                          "status": "NORMAL_OPEN",
                          "last_updated": 1705549500000}}
        """
        url = f"https://api.upstox.com/v2/market/status/{exchange}"
        logger.debug("GET %s", url)
        response = self._http.get(url)
        _raise_for_status(response)
        return response.json()  # type: ignore[return-value]

    def get_exchange_status(self, exchange: str = "NSE") -> dict[str, Any]:
        """Alias for get_market_status(). Fetches status for one exchange."""
        return self.get_market_status(exchange)

    # ── WebSocket ─────────────────────────────────────────────────────

    def get_ws_authorize_url(self) -> str:
        """
        Fetch the one-time authorized ``wss://`` URL for Market Data Feed V3.

        This URL is single-use (the embedded code expires after one connection).
        Call this immediately before opening the WebSocket — do not cache it.

        Returns
        -------
        str
            A ``wss://`` URL ready to pass to a WebSocket client.
        """
        data = self._get("/feed/market-data-feed/authorize")
        try:
            return data["data"]["authorized_redirect_uri"]  # type: ignore[return-value]
        except (KeyError, TypeError) as exc:
            raise UpstoxAPIError(200, f"Unexpected authorize response: {data}") from exc

    # ── Cleanup ───────────────────────────────────────────────────────

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "UpstoxClientV3":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
