"""
async_client.py — Async httpx wrapper around the Upstox v2 REST API.

Wraps ``httpx.AsyncClient`` with:

- Concurrency-aware rate limiting (via ``aiolimiter.AsyncLimiter``).
- Automatic retry with exponential backoff on 5xx and network errors (via ``tenacity``).
- Proper 401/429 handling that surfaces friendly error messages.

Usage::

    import asyncio
    from upstox_historical.async_client import AsyncUpstoxClient

    async def main():
        async with AsyncUpstoxClient() as client:
            raw = await client.get_historical_candles(
                "NSE_INDEX|Nifty 50", "day", "2024-01-01", "2024-12-31"
            )

    asyncio.run(main())
"""
from __future__ import annotations

import logging
from typing import Any

import httpx
from aiolimiter import AsyncLimiter
from tenacity import (
    AsyncRetrying,
    before_sleep_log,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from upstox_historical.client import UpstoxAPIError, UpstoxAuthError
from upstox_historical.config import Settings, get_settings

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30.0
_DEFAULT_MAX_CONCURRENCY = 5
_DEFAULT_RATE_LIMIT = 20       # requests per window
_DEFAULT_RATE_WINDOW = 1.0     # seconds
_DEFAULT_RETRY_ATTEMPTS = 5


class AsyncUpstoxClient:
    """
    Async client for the Upstox v2 API with built-in rate limiting and retries.

    Parameters
    ----------
    access_token : str, optional
        Override access token. Defaults to ``UPSTOX_ACCESS_TOKEN`` from .env.
    settings : Settings, optional
        Override settings instance.
    timeout : float
        Per-request timeout in seconds.
    max_concurrency : int
        Maximum number of concurrent in-flight requests.
    rate_limit : int
        Maximum requests per ``rate_window`` seconds.
    rate_window : float
        Window length for rate limiting (seconds).
    retry_attempts : int
        Number of retry attempts for transient failures.
    """

    def __init__(
        self,
        access_token: str | None = None,
        settings: Settings | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
        max_concurrency: int = _DEFAULT_MAX_CONCURRENCY,
        rate_limit: int = _DEFAULT_RATE_LIMIT,
        rate_window: float = _DEFAULT_RATE_WINDOW,
        retry_attempts: int = _DEFAULT_RETRY_ATTEMPTS,
    ) -> None:
        self._settings = settings or get_settings()
        self._token = access_token or self._settings.access_token

        if not self._token:
            raise UpstoxAuthError(
                "No access token found. Set UPSTOX_ACCESS_TOKEN in your .env file "
                "or pass access_token= explicitly. See README for OAuth flow."
            )

        self._base_url = self._settings.base_url
        self._timeout = timeout

        # Rate limiter: allow `rate_limit` requests per `rate_window` seconds
        self._limiter = AsyncLimiter(rate_limit, rate_window)

        # Concurrency cap orthogonal to rate — guards against client-side overload
        import asyncio
        self._sem = asyncio.Semaphore(max_concurrency)

        self._retry_attempts = retry_attempts

        self._http: httpx.AsyncClient | None = None
        logger.debug(
            "AsyncUpstoxClient configured | base=%s | concurrency=%d | %d req / %.1fs | retries=%d",
            self._base_url, max_concurrency, rate_limit, rate_window, retry_attempts,
        )

    # ── lifecycle ─────────────────────────────────────────────────────

    async def __aenter__(self) -> "AsyncUpstoxClient":
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            headers=self._build_headers(),
            timeout=self._timeout,
        )
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def aclose(self) -> None:
        """Explicit close (in case you don't use ``async with``)."""
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    # ── internal ──────────────────────────────────────────────────────

    def _build_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
            "Api-Version": "2.0",
        }

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                base_url=self._base_url,
                headers=self._build_headers(),
                timeout=self._timeout,
            )
        return self._http

    async def _get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Rate-limited, retried GET. Returns parsed JSON dict."""
        client = await self._ensure_client()

        # Transient errors we retry on: 5xx, timeouts, connection errors.
        # 429 is retried too, but with longer backoff.
        retryable = (
            httpx.TimeoutException,
            httpx.ConnectError,
            httpx.RemoteProtocolError,
            _TransientHTTPError,
        )

        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self._retry_attempts),
            wait=wait_exponential(multiplier=1, min=1, max=30),
            retry=retry_if_exception_type(retryable),
            before_sleep=before_sleep_log(logger, logging.WARNING),
            reraise=True,
        ):
            with attempt:
                async with self._sem:
                    async with self._limiter:
                        logger.debug("GET %s  params=%s", path, params)
                        resp = await client.get(path, params=params)
                        _raise_for_status(resp)
                        return resp.json()  # type: ignore[return-value]

        # tenacity always raises on final failure; this line is unreachable
        raise RuntimeError("retry loop exited without result")  # pragma: no cover

    # ── public API (mirrors sync UpstoxClient) ────────────────────────

    async def get_historical_candles(
        self,
        instrument_key: str,
        interval: str,
        from_date: str,
        to_date: str,
    ) -> dict[str, Any]:
        """Async version of ``UpstoxClient.get_historical_candles``."""
        path = f"/historical-candle/{instrument_key}/{interval}/{to_date}/{from_date}"
        return await self._get(path)

    async def get_intraday_candles(
        self,
        instrument_key: str,
        interval: str,
    ) -> dict[str, Any]:
        """Async version of ``UpstoxClient.get_intraday_candles``."""
        path = f"/historical-candle/intraday/{instrument_key}/{interval}"
        return await self._get(path)

    async def search_instruments(self, query: str) -> dict[str, Any]:
        """Async instrument search."""
        return await self._get("/instruments/search", params={"q": query})

    async def get_market_quote(self, instrument_keys: list[str]) -> dict[str, Any]:
        """Async LTP quote."""
        return await self._get(
            "/market-quote/ltp",
            params={"instrument_key": ",".join(instrument_keys)},
        )


# ── helpers ──────────────────────────────────────────────────────────

class _TransientHTTPError(Exception):
    """Internal marker exception used to trigger tenacity retries on 5xx/429."""


def _raise_for_status(response: httpx.Response) -> None:
    """
    Map an httpx Response to our exception hierarchy.

    401 → UpstoxAuthError (NOT retried, user action needed)
    429 → _TransientHTTPError (retried with backoff)
    5xx → _TransientHTTPError (retried with backoff)
    other errors → UpstoxAPIError (NOT retried)
    """
    sc = response.status_code
    if sc == 401:
        raise UpstoxAuthError(
            "401 Unauthorised — your access token is invalid or expired. "
            "Re-run: uv run upstox-fetch login"
        )
    if sc == 429:
        logger.warning("429 rate-limit from Upstox — backing off and retrying")
        raise _TransientHTTPError("429 rate-limited")
    if 500 <= sc < 600:
        logger.warning("%d server error from Upstox — retrying", sc)
        raise _TransientHTTPError(f"{sc} server error")
    if response.is_error:
        try:
            msg = response.json().get("errors", [{}])[0].get("message", response.text)
        except Exception:
            msg = response.text
        raise UpstoxAPIError(sc, msg)
