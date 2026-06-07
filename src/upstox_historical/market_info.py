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
    token: str | None = None,
) -> list[MarketStatus]:
    """
    Return the open/close status of all exchange segments.

    Returns
    -------
    list[MarketStatus]
        One entry per segment (NSE_EQ, NSE_FO, NSE_INDEX, BSE_EQ, ...).

    Example
    -------
    >>> statuses = get_market_status_all()
    >>> for s in statuses:
    ...     print(s.segment, s.status, s.is_open)
    NSE_EQ NORMAL_OPEN True
    NSE_FO NORMAL_OPEN True
    """
    settings = get_settings()
    with UpstoxClientV3(token=token, settings=settings) as client:
        raw = client.get_exchange_status()

    # Response shape varies — handle both list and dict forms
    data = raw.get("data", raw)

    results: list[MarketStatus] = []
    if isinstance(data, list):
        for item in data:
            results.append(MarketStatus(
                segment=item.get("exchange", item.get("segment", "UNKNOWN")),
                status=item.get("market_status", item.get("status", "UNKNOWN")),
            ))
    elif isinstance(data, dict):
        for segment, status in data.items():
            if isinstance(status, str):
                results.append(MarketStatus(segment=segment, status=status))
            elif isinstance(status, dict):
                results.append(MarketStatus(
                    segment=segment,
                    status=status.get("market_status", status.get("status", "UNKNOWN")),
                ))

    logger.debug("Market status: %d segments", len(results))
    return results


def is_market_open(
    segment: str = "NSE_EQ",
    *,
    token: str | None = None,
) -> bool:
    """
    Return True when the given exchange segment is open for trading.

    This is the primary gate check used by the pipeline before any
    live data fetch or WebSocket operation.

    Parameters
    ----------
    segment : str
        Exchange segment to check. Default: ``"NSE_EQ"``.
        Common values: ``"NSE_EQ"`` | ``"NSE_FO"`` | ``"NSE_INDEX"``
    token : str, optional

    Returns
    -------
    bool

    Example
    -------
    >>> if is_market_open():
    ...     price = get_ltp("NSE_INDEX|Nifty 50")
    """
    statuses = get_market_status_all(token=token)

    for s in statuses:
        if s.segment.upper() == segment.upper():
            logger.debug("Segment %s status=%s open=%s", s.segment, s.status, s.is_open)
            return s.is_open

    # Segment not found in response — log a warning and assume closed (safe default)
    logger.warning(
        "Segment '%s' not found in market status response. Assuming closed.", segment
    )
    return False


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
