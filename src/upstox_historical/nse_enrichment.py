"""
nse_enrichment.py — Fetches NSE Bhav Copy data to enrich Upstox OHLCV candles
               with: Prev Close, Trades, Deliverable Volume, %Deliverble, Turnover.

Data sources
------------
- NSE Bhav Copy (equity delivery data):
      https://archives.nseindia.com/products/content/sec_bhavdata_full_<DDMMYYYY>.csv
  Contains: SYMBOL, SERIES, PREV_CLOSE, TRADES, DELIV_QTY, DELIV_PER for each trading day.

- Turnover is computed locally from VWAP × Volume per candle.

Caching
-------
Parsed Bhav Copies are cached to disk under
``~/.cache/upstox-historical/bhav/`` (one parquet per trading date,
plus ``.miss`` marker files for non-trading days). Subsequent runs that
need the same date get a cache hit and skip the NSE round-trip.

Progress reporting
------------------
``enrich()`` accepts an optional ``progress_cb`` callable that is invoked
after each Bhav Copy lookup (cache hit or network fetch). The callback
receives ``(completed, total, current_date)`` — the CLI uses this to
render a Rich progress bar instead of per-date INFO log lines.

Logic
-----
For daily/weekly/monthly intervals:
    - Prev Close, Trades, Deliverable Volume, %Deliverble come directly from Bhav Copy.

For intraday intervals (1min, 5min, 30min etc.):
    - Bhav Copy values are at day level → broadcast to all candles of that date.

Turnover is always computed locally per candle:
    turnover = round(vwap * volume)    [in Rupees, matching NSE convention]
"""
from __future__ import annotations

import io
import logging
import time
from datetime import date
from typing import Callable, Optional

import httpx
import pandas as pd

from upstox_historical.cache import (
    bhav_load,
    bhav_mark_miss,
    bhav_store,
)

logger = logging.getLogger(__name__)

# Type alias: called as progress_cb(done, total, current_date)
BhavProgressCB = Callable[[int, int, date], None]

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

_REQUEST_DELAY = 1.0   # seconds between NSE network requests (when not cached)
_TIMEOUT = 20.0        # seconds


# ── Process-local cache (hot path) layered on top of disk cache ──────────────
# Avoids re-reading the same parquet many times within one process.
_bhav_memcache: dict[str, pd.DataFrame] = {}


def _fetch_bhav(trade_date: date) -> Optional[pd.DataFrame]:
    """
    Return the Bhav Copy DataFrame for `trade_date`.

    Lookup order:
      1. Process-local memory cache.
      2. On-disk parquet cache.
      3. Miss marker (confirmed non-trading day) — return None without network call.
      4. NSE network fetch.

    Returns None if the date is not a trading day.
    """
    mem_key = trade_date.isoformat()
    if mem_key in _bhav_memcache:
        return _bhav_memcache[mem_key]

    # Disk cache hit?
    cached = bhav_load(trade_date)
    if cached is not None:
        _bhav_memcache[mem_key] = cached
        return cached

    # Network fetch
    date_str = trade_date.strftime("%d%m%Y")
    url = _BHAV_URL.format(date=date_str)
    logger.debug("NSE Bhav Copy: fetching %s", url)

    try:
        with httpx.Client(headers=_HEADERS, timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = client.get(url)
        if resp.status_code == 404:
            logger.debug("Bhav Copy not found for %s (holiday/weekend)", trade_date)
            bhav_mark_miss(trade_date)
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

    # Store to both caches
    _bhav_memcache[mem_key] = df
    bhav_store(trade_date, df)

    # Politeness delay only when we actually hit the network
    time.sleep(_REQUEST_DELAY)
    return df


def _unique_trading_dates(df: pd.DataFrame) -> list[date]:
    """Extract sorted unique calendar dates from the timestamp column."""
    return sorted(df["timestamp"].dt.date.unique())


def _get_bhav_for_dates(
    dates: list[date],
    progress_cb: Optional[BhavProgressCB] = None,
) -> pd.DataFrame:
    """
    Fetch Bhav Copy for all given dates, emitting progress updates.

    Parameters
    ----------
    dates : list[date]
        Trading dates to enrich.
    progress_cb : callable, optional
        Invoked as ``progress_cb(done, total, current_date)`` after each
        date is resolved (from cache or network). Allows the CLI to render
        a progress bar without coupling this module to Rich.
    """
    total = len(dates)
    frames: list[pd.DataFrame] = []
    for idx, d in enumerate(dates, start=1):
        bdf = _fetch_bhav(d)
        if bdf is not None:
            frames.append(bdf)
        if progress_cb is not None:
            progress_cb(idx, total, d)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def _extract_symbol(instrument_key: str) -> str:
    """
    Extract a human-readable ticker symbol from an Upstox instrument key.
    """
    if "|" in instrument_key:
        right = instrument_key.split("|", 1)[1]
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
    progress_cb: Optional[BhavProgressCB] = None,
) -> pd.DataFrame:
    """
    Enrich an Upstox OHLCV DataFrame with NSE Bhav Copy columns.

    Parameters
    ----------
    df : pd.DataFrame
        Output of ``HistoricalFetcher.fetch()`` — must have columns
        timestamp, open, high, low, close, volume, vwap.
    instrument_key : str
        e.g. ``"NSE_EQ|INE002A01018"``.
    symbol : str, optional
        Override ticker symbol (e.g. ``"RELIANCE"``). **Required for
        ISIN-keyed equities** since Bhav Copy is keyed by ticker, not ISIN.
    series : str, optional
        Override series (e.g. ``"EQ"``).
    is_intraday : bool
        If True, Bhav Copy values are broadcast to all candles of the same
        trading day.
    progress_cb : callable, optional
        Invoked as ``progress_cb(done, total, current_date)`` after each
        trading date's Bhav Copy is resolved. Used by the CLI for the
        enrichment progress bar.

    Returns
    -------
    pd.DataFrame
        Columns matching Sample.csv:
        timestamp, Symbol, Series, Prev Close, open, high, low, close,
        volume, vwap, Turnover, Trades, Deliverable Volume, %Deliverble.
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
    if "vwap" in df.columns:
        df["Turnover"] = (df["vwap"] * df["volume"]).round(0).astype("int64")
    else:
        typical = (df["high"] + df["low"] + df["close"]) / 3
        df["Turnover"] = (typical * df["volume"]).round(0).astype("int64")

    # ── 3. NSE Bhav Copy enrichment ───────────────────────────────────
    trading_dates = _unique_trading_dates(df)
    logger.info(
        "NSE Enrichment: resolving Bhav Copy for %d trading date(s): %s … %s",
        len(trading_dates),
        trading_dates[0] if trading_dates else "–",
        trading_dates[-1] if trading_dates else "–",
    )

    bhav = _get_bhav_for_dates(trading_dates, progress_cb=progress_cb)

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
    df["_trade_date"] = df["timestamp"].dt.date

    bhav_day = bhav_sym[
        ["_trade_date", "Prev Close", "Trades", "Deliverable Volume", "%Deliverble"]
    ].drop_duplicates(subset=["_trade_date"])

    df = df.merge(bhav_day, on="_trade_date", how="left")
    df = df.drop(columns=["_trade_date"])

    logger.info("NSE Enrichment complete. Enriched %d rows.", len(df))
    return _finalise_columns(df)


def _finalise_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Reorder and capitalize columns to match Sample.csv layout and drop open_interest.

    OHLCV columns are capitalized on the way out (Open, High, Low, Close, Volume, VWAP)
    to match standard financial-data conventions. Internal processing uses lowercase
    to stay consistent with Upstox API field names; this is the one translation point.
    """
    if "open_interest" in df.columns:
        df = df.drop(columns=["open_interest"])

    # Capitalize OHLCV columns (VWAP is all-caps by convention, not Vwap)
    rename_map = {
        "open":   "Open",
        "high":   "High",
        "low":    "Low",
        "close":  "Close",
        "volume": "Volume",
        "vwap":   "VWAP",
    }
    df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})

    col_order = [
        "timestamp",
        "Symbol",
        "Series",
        "Prev Close",
        "Open",
        "High",
        "Low",
        "Close",
        "Volume",
        "VWAP",
        "Turnover",
        "Trades",
        "Deliverable Volume",
        "%Deliverble",
    ]
    col_order = [c for c in col_order if c in df.columns]
    extras = [c for c in df.columns if c not in col_order]
    return df[col_order + extras]
