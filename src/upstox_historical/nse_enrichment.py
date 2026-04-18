"""
nse_enrichment.py — Fetches NSE Bhav Copy data to enrich Upstox OHLCV candles
               with: Prev Close, Trades, Deliverable Volume, %Deliverble, Turnover.

Data sources
------------
- NSE Bhav Copy (equity delivery data):
      https://archives.nseindia.com/products/content/sec_bhavdata_full_<DDMMYYYY>.csv
  Contains: SYMBOL, SERIES, PREV_CLOSE, TRADES, DELIV_QTY, DELIV_PER for each trading day.

- Turnover is computed locally from VWAP × Volume per candle.

Logic
-----
For daily/weekly/monthly intervals:
    - Prev Close, Trades, Deliverable Volume, %Deliverble come directly from Bhav Copy.

For intraday intervals (1min, 5min, 30min etc.):
    - Bhav Copy values are at day level → broadcast to all candles of that date.
    - Trades: use the daily total (same value repeated for every candle of that day).
    - Deliverable Volume: same daily value repeated.
    - %Deliverble: same daily value repeated.
    - Prev Close: close of previous trading day (from Bhav Copy).

Turnover is always computed locally per candle:
    turnover = round(vwap * volume)    [in Rupees, matching NSE convention]
"""
from __future__ import annotations

import io
import logging
import time
from datetime import date, timedelta
from functools import lru_cache
from typing import Callable, Optional

import httpx
import pandas as pd

logger = logging.getLogger(__name__)

# ── NSE Bhav Copy URL template ────────────────────────────────────────────────
_BHAV_URL = (
    "https://archives.nseindia.com/products/content/sec_bhavdata_full_{date}.csv"
)

# NSE Bhav Copy column names (raw) → our names
_BHAV_COLS_RENAME = {
    "SYMBOL":         "Symbol",
    "SERIES":         "Series",
    "PREV_CLOSE":     "Prev Close",
    "NO_OF_TRADES":   "Trades",
    "DELIV_QTY":      "Deliverable Volume",
    "DELIV_PER":      "%Deliverble",
}

# Columns we keep from the Bhav Copy
_BHAV_KEEP = ["Symbol", "Series", "Prev Close", "Trades", "Deliverable Volume", "%Deliverble", "_trade_date"]

# NSE request headers (required to avoid 403)
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Encoding": "gzip, deflate",
    "Accept": "*/*",
    "Connection": "keep-alive",
    "Referer": "https://www.nseindia.com/",
}

_REQUEST_DELAY = 1.0   # seconds between NSE requests (be polite)
_TIMEOUT = 20.0        # seconds


# ── Internal cache: date-string → DataFrame (one Bhav Copy per day) ──────────
_bhav_cache: dict[str, pd.DataFrame] = {}


def _fetch_bhav(trade_date: date) -> Optional[pd.DataFrame]:
    """
    Download and parse NSE Bhav Copy for a single trading date.
    Returns None if the date is not a trading day (file not found).
    Caches results in memory to avoid duplicate downloads.
    """
    date_str = trade_date.strftime("%d%m%Y")
    cache_key = date_str

    if cache_key in _bhav_cache:
        return _bhav_cache[cache_key]

    url = _BHAV_URL.format(date=date_str)
    logger.info("NSE Bhav Copy: fetching %s", url)

    try:
        with httpx.Client(headers=_HEADERS, timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = client.get(url)
        if resp.status_code == 404:
            logger.debug("Bhav Copy not found for %s (holiday/weekend)", trade_date)
            return None
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("Bhav Copy fetch failed for %s: %s", trade_date, exc)
        return None

    try:
        df = pd.read_csv(io.StringIO(resp.text))
    except Exception as exc:
        logger.warning("Bhav Copy parse failed for %s: %s", trade_date, exc)
        return None

    # Strip whitespace from column names and string columns
    df.columns = df.columns.str.strip()
    for col in df.select_dtypes("object").columns:
        df[col] = df[col].str.strip()

    # Rename to our names
    df = df.rename(columns=_BHAV_COLS_RENAME)

    # Keep only needed columns (some may not be present on all dates)
    cols_present = [c for c in list(_BHAV_COLS_RENAME.values()) if c in df.columns]
    df = df[cols_present].copy()
    df["_trade_date"] = trade_date

    _bhav_cache[cache_key] = df
    time.sleep(_REQUEST_DELAY)
    return df


def _unique_trading_dates(df: pd.DataFrame) -> list[date]:
    """Extract sorted unique calendar dates from the timestamp column."""
    return sorted(df["timestamp"].dt.date.unique())


def _get_bhav_for_dates(
    dates: list[date],
    on_progress: "Callable[[int, int], None] | None" = None,
) -> pd.DataFrame:
    """
    Fetch Bhav Copy for all given dates.
    Returns a combined DataFrame with _trade_date column.

    Parameters
    ----------
    on_progress : callable, optional
        Called as on_progress(current, total) after each date is fetched.
    """
    frames: list[pd.DataFrame] = []
    total = len(dates)
    for idx, d in enumerate(dates, start=1):
        bdf = _fetch_bhav(d)
        if bdf is not None:
            frames.append(bdf)
        if on_progress:
            on_progress(idx, total)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def _extract_symbol(instrument_key: str) -> str:
    """
    Extract a human-readable ticker symbol from an Upstox instrument key.

    Examples
    --------
    "NSE_EQ|INE002A01018"   → looked up from ISIN (or user must configure)
    "NSE_INDEX|Nifty 50"    → "Nifty 50"
    "NSE_EQ|RELIANCE"       → "RELIANCE"  (if trading_symbol format)
    """
    if "|" in instrument_key:
        right = instrument_key.split("|", 1)[1]
        # If it looks like an ISIN (INE...) we return as-is for now;
        # the Bhav Copy merge will use the Symbol already configured
        return right
    return instrument_key


def _extract_series(instrument_key: str) -> str:
    """
    Derive the NSE series from an instrument key.
    NSE_EQ → EQ, NSE_INDEX → INDEX, etc.
    """
    prefix = instrument_key.split("|", 1)[0].upper()
    mapping = {
        "NSE_EQ":    "EQ",
        "NSE_BE":    "BE",
        "NSE_SM":    "SM",
        "NSE_INDEX": "INDEX",
        "BSE_INDEX": "INDEX",
        "BSE_EQ":    "EQ",
    }
    return mapping.get(prefix, "EQ")


def enrich(
    df: pd.DataFrame,
    instrument_key: str,
    symbol: Optional[str] = None,
    series: Optional[str] = None,
    is_intraday: bool = False,
    on_bhav_progress: "Callable[[int, int], None] | None" = None,
    on_merge_start: "Callable[[], None] | None" = None,
) -> pd.DataFrame:
    """
    Enrich an Upstox OHLCV DataFrame with NSE Bhav Copy columns.

    Parameters
    ----------
    df : pd.DataFrame
        Output of HistoricalFetcher.fetch() — must have columns:
        timestamp, open, high, low, close, volume, vwap
    instrument_key : str
        e.g. "NSE_EQ|INE002A01018"
    symbol : str, optional
        Override ticker symbol (e.g. "RELIANCE"). If not provided, derived
        from instrument_key. **Strongly recommended** for ISIN-keyed equities.
    series : str, optional
        Override series (e.g. "EQ"). If not provided, derived from instrument_key.
    is_intraday : bool
        If True, Bhav Copy values (Trades, Deliverable Volume, %Deliverble)
        are broadcast to all candles belonging to the same trading day.
    on_bhav_progress : callable, optional
        Called as on_bhav_progress(current, total) after each Bhav Copy date fetch.
    on_merge_start : callable, optional
        Called when the merge phase begins.

    Returns
    -------
    pd.DataFrame
        Columns matching Sample.csv:
        timestamp, Symbol, Series, Prev Close, open, high, low, close,
        volume, vwap, Turnover, Trades, Deliverable Volume, %Deliverble
    """
    if df.empty:
        return df

    df = df.copy()

    # ── 1. Symbol & Series ────────────────────────────────────────────
    resolved_symbol = symbol or _extract_symbol(instrument_key)
    resolved_series = series or _extract_series(instrument_key)
    df["Symbol"] = resolved_symbol
    df["Series"] = resolved_series

    # ── 2. Turnover (computed locally: vwap × volume per candle) ─────
    # NSE reports Turnover in Rupees. vwap * volume gives Rupee value per candle.
    if "vwap" in df.columns:
        df["Turnover"] = (df["vwap"] * df["volume"]).round(0).astype("int64")
    else:
        # Fallback: typical_price × volume
        typical = (df["high"] + df["low"] + df["close"]) / 3
        df["Turnover"] = (typical * df["volume"]).round(0).astype("int64")

    # ── 3. NSE Bhav Copy enrichment ───────────────────────────────────
    trading_dates = _unique_trading_dates(df)
    logger.info(
        "NSE Enrichment: fetching Bhav Copy for %d trading date(s): %s … %s",
        len(trading_dates),
        trading_dates[0] if trading_dates else "–",
        trading_dates[-1] if trading_dates else "–",
    )

    bhav = _get_bhav_for_dates(trading_dates, on_progress=on_bhav_progress)

    if bhav.empty:
        logger.warning(
            "No Bhav Copy data retrieved — Prev Close, Trades, Deliverable columns will be NaN."
        )
        df["Prev Close"]          = float("nan")
        df["Trades"]              = pd.NA
        df["Deliverable Volume"]  = pd.NA
        df["%Deliverble"]         = float("nan")
        return _finalise_columns(df)

    # Filter to our symbol + series
    bhav_sym = bhav[
        (bhav["Symbol"] == resolved_symbol) &
        (bhav["Series"] == resolved_series)
    ].copy()

    if bhav_sym.empty:
        logger.warning(
            "Symbol '%s' / Series '%s' not found in Bhav Copy — "
            "check that `symbol` matches the NSE ticker exactly.",
            resolved_symbol, resolved_series,
        )
        df["Prev Close"]          = float("nan")
        df["Trades"]              = pd.NA
        df["Deliverable Volume"]  = pd.NA
        df["%Deliverble"]         = float("nan")
        return _finalise_columns(df)

    # ── 4. Merge Bhav Copy onto candles by date ───────────────────────
    if on_merge_start:
        on_merge_start()

    df["_trade_date"] = df["timestamp"].dt.date

    bhav_day = bhav_sym[
        ["_trade_date", "Prev Close", "Trades", "Deliverable Volume", "%Deliverble"]
    ].drop_duplicates(subset=["_trade_date"])

    df = df.merge(bhav_day, on="_trade_date", how="left")
    df = df.drop(columns=["_trade_date"])

    # For intraday: Prev Close = close of the PREVIOUS candle (per day)
    # but since Bhav Copy already gives yesterday's close, keep it as-is —
    # it is the correct "previous session close" for the day, broadcast to all
    # intraday rows of that date.  This matches the Sample.csv behaviour.

    logger.info("NSE Enrichment complete. Enriched %d rows.", len(df))
    return _finalise_columns(df)


def _finalise_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Reorder columns to match Sample.csv layout and drop open_interest.
    """
    # Drop open_interest (not needed)
    if "open_interest" in df.columns:
        df = df.drop(columns=["open_interest"])

    # Desired column order matching Sample.csv
    col_order = [
        "timestamp",      # will be renamed to Date by fetcher
        "Symbol",
        "Series",
        "Prev Close",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "vwap",
        "Turnover",
        "Trades",
        "Deliverable Volume",
        "%Deliverble",
    ]

    # Only keep columns that exist
    col_order = [c for c in col_order if c in df.columns]
    # Append any extra columns not in the order (shouldn't happen normally)
    extras = [c for c in df.columns if c not in col_order]
    return df[col_order + extras]
