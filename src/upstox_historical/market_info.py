"""
market_info.py — clean public interface for live market data.

All functions here use the Analytics Token (1-year) via UpstoxClientV3.
No daily OAuth renewal needed.

Typical usage::

    from upstox_historical.market_info import get_ltp, get_ohlc, is_market_open

    price = get_ltp("NSE_INDEX|Nifty 50")          # 24523.45
    ohlc  = get_ohlc("NSE_EQ|INE002A01018")        # OHLCQuote(...)
    open_ = is_market_open()                        # True / False
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from upstox_historical.client import UpstoxClientV3
from upstox_historical.config import get_settings

logger = logging.getLogger(__name__)


# ── Return types ──────────────────────────────────────────────────────

@dataclass
class OHLCQuote:
    """Single instrument OHLC snapshot."""
    instrument_key: str
    open: float
    high: float
    low: float
    close: float        # last traded price (same as LTP in OHLC response)
    volume: int
    oi: int             # open interest (0 for equity, non-zero for F&O)


@dataclass
class MarketStatus:
    """Exchange segment open/close state."""
    segment: str        # e.g. "NSE_EQ", "NSE_FO", "NSE_INDEX"
    status: str         # e.g. "NORMAL_OPEN", "CLOSED", "PRE_OPEN"

    @property
    def is_open(self) -> bool:
        """True when the segment is in a tradeable state."""
        return "OPEN" in self.status.upper()


# ── Public functions ──────────────────────────────────────────────────

def get_ltp(
    instrument_key: str,
    *,
    token: str | None = None,
) -> float:
    """
    Return the Last Traded Price for a single instrument.

    Uses the Analytics Token — no daily reauth needed.

    Parameters
    ----------
    instrument_key : str
        e.g. ``"NSE_INDEX|Nifty 50"`` or ``"NSE_EQ|INE002A01018"``
    token : str, optional
        Override token. Defaults to UPSTOX_ANALYTICS_TOKEN from .env.

    Returns
    -------
    float
        Last traded price as a scalar.

    Raises
    ------
    KeyError
        If the instrument key is not found in the response.

    Example
    -------
    >>> price = get_ltp("NSE_INDEX|Nifty 50")
    >>> print(f"Nifty LTP: ₹{price:,.2f}")
    Nifty LTP: ₹24,523.45
    """
    settings = get_settings()
    with UpstoxClientV3(token=token, settings=settings) as client:
        raw = client.get_ltp([instrument_key])

    # Response shape: {"status": "success", "data": {"NSE_INDEX:Nifty 50": {"last_price": 24523.45}}}
    data: dict[str, Any] = raw.get("data", {})
    # Key uses ":" separator in response even if we sent "|"
    key = instrument_key.replace("|", ":")
    try:
        ltp = float(data[key]["last_price"])
    except KeyError:
        # Try the original key format as fallback
        ltp = float(next(iter(data.values()))["last_price"])

    logger.debug("LTP %s = %.4f", instrument_key, ltp)
    return ltp


def get_ltp_many(
    instrument_keys: list[str],
    *,
    token: str | None = None,
) -> dict[str, float]:
    """
    Return LTP for multiple instruments in a single API call (up to 500).

    Parameters
    ----------
    instrument_keys : list[str]

    Returns
    -------
    dict[str, float]
        Mapping of instrument_key → last_price.
    """
    settings = get_settings()
    with UpstoxClientV3(token=token, settings=settings) as client:
        raw = client.get_ltp(instrument_keys)

    data: dict[str, Any] = raw.get("data", {})
    result: dict[str, float] = {}
    for raw_key, payload in data.items():
        # Normalise back to "|" separator
        norm_key = raw_key.replace(":", "|")
        result[norm_key] = float(payload["last_price"])

    logger.debug("LTP batch: %d instruments", len(result))
    return result


def get_ohlc(
    instrument_key: str,
    interval: str = "1d",
    *,
    token: str | None = None,
) -> OHLCQuote:
    """
    Return today's OHLC snapshot for a single instrument.

    Parameters
    ----------
    instrument_key : str
    interval : str
        ``"1d"`` (default) | ``"1week"`` | ``"1month"``
    token : str, optional

    Returns
    -------
    OHLCQuote

    Example
    -------
    >>> q = get_ohlc("NSE_EQ|INE002A01018")
    >>> print(q.close, q.volume)
    """
    settings = get_settings()
    with UpstoxClientV3(token=token, settings=settings) as client:
        raw = client.get_ohlc([instrument_key], interval=interval)

    data: dict[str, Any] = raw.get("data", {})
    key = instrument_key.replace("|", ":")
    try:
        payload = data[key]
    except KeyError:
        payload = next(iter(data.values()))

    ohlc = payload.get("ohlc", payload)
    return OHLCQuote(
        instrument_key=instrument_key,
        open=float(ohlc.get("open", 0)),
        high=float(ohlc.get("high", 0)),
        low=float(ohlc.get("low", 0)),
        close=float(ohlc.get("close", payload.get("last_price", 0))),
        volume=int(payload.get("volume", 0)),
        oi=int(payload.get("oi", 0)),
    )


def get_market_status_all(
    *,
    exchanges: list[str] | None = None,
    token: str | None = None,
) -> list[MarketStatus]:
    """
    Return the open/close status for a set of exchanges.

    The Upstox endpoint returns one exchange per call (``/v2/market/status/{exchange}``),
    so this queries each requested exchange and collects the results.

    Parameters
    ----------
    exchanges : list[str], optional
        Exchange codes to query. Defaults to ["NSE", "BSE", "NFO", "MCX"].
    token : str, optional

    Returns
    -------
    list[MarketStatus]
        One entry per exchange queried.

    Example
    -------
    >>> statuses = get_market_status_all()
    >>> for s in statuses:
    ...     print(s.segment, s.status, s.is_open)
    NSE NORMAL_OPEN True
    BSE NORMAL_OPEN True
    """
    exchanges = exchanges or ["NSE", "BSE", "NFO", "MCX"]
    settings = get_settings()
    results: list[MarketStatus] = []

    with UpstoxClientV3(token=token, settings=settings) as client:
        for exch in exchanges:
            try:
                raw = client.get_market_status(exch)
            except Exception as exc:  # one bad exchange shouldn't sink the rest
                logger.warning("Market status fetch failed for %s: %s", exch, exc)
                continue
            # Response: {"status": "success", "data": {"exchange": "NSE",
            #            "status": "NORMAL_OPEN", "last_updated": ...}}
            data = raw.get("data", {})
            if isinstance(data, dict) and data:
                results.append(MarketStatus(
                    segment=data.get("exchange", exch),
                    status=data.get("status", "UNKNOWN"),
                ))

    logger.debug("Market status: %d exchanges", len(results))
    return results


def is_market_open(
    exchange: str = "NSE",
    *,
    token: str | None = None,
) -> bool:
    """
    Return True when the given exchange is open for trading.

    This is the primary gate check used by the pipeline before any
    live data fetch or WebSocket operation.

    Parameters
    ----------
    exchange : str
        Exchange code to check. Default: ``"NSE"``.
        Common values: ``"NSE"`` | ``"BSE"`` | ``"NFO"`` | ``"MCX"``
    token : str, optional

    Returns
    -------
    bool

    Example
    -------
    >>> if is_market_open():
    ...     price = get_ltp("NSE_INDEX|Nifty 50")
    """
    settings = get_settings()
    with UpstoxClientV3(token=token, settings=settings) as client:
        try:
            raw = client.get_market_status(exchange)
        except Exception as exc:
            logger.warning("Market status check failed for %s: %s", exchange, exc)
            return False

    data = raw.get("data", {})
    status = data.get("status", "") if isinstance(data, dict) else ""
    is_open = "OPEN" in status.upper()
    logger.debug("Exchange %s status=%s open=%s", exchange, status, is_open)
    return is_open


def get_ws_authorize_url(*, token: str | None = None) -> str:
    """
    Fetch the one-time authorized ``wss://`` URL for the live feed.

    Thin wrapper around UpstoxClientV3.get_ws_authorize_url().
    Call immediately before opening the WebSocket — URL is single-use.

    Returns
    -------
    str
        ``wss://`` URL ready to pass to stream.LiveStream.
    """
    settings = get_settings()
    with UpstoxClientV3(token=token, settings=settings) as client:
        url = client.get_ws_authorize_url()
    logger.debug("WebSocket authorize URL obtained")
    return url
