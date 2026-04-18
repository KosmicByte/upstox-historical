"""
fetcher.py — high-level interface that converts raw API responses
             into clean pandas DataFrames and saves them to disk.

Chunked fetching
----------------
Upstox enforces per-request date-range limits:

    1minute  → max 1 month per request
    30minute → max 1 year  per request
    day      → max 1 year  per request
    week     → max 10 years per request
    month    → max 10 years per request

fetch() and fetch_and_save() automatically split large date ranges
into chunks, fetch each chunk with a small delay, and stitch results
together — so you can request 10 years of 5-min data in one call.
"""
from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta
from pathlib import Path
from typing import Callable, Iterator

import pandas as pd

from upstox_historical.client import UpstoxClient
from upstox_historical.models import HistoricalResponse, Interval
from upstox_historical.nse_enrichment import enrich

logger = logging.getLogger(__name__)

# ── Column names ──────────────────────────────────────────────────────
_CANDLE_COLS = ["timestamp", "open", "high", "low", "close", "volume", "open_interest"]

# ── Native vs resampled intervals ────────────────────────────────────
_NATIVE_INTERVALS = {"1minute", "30minute", "day", "week", "month"}

_RESAMPLE_MAP: dict[str, str] = {
    "2minute":  "2min",
    "3minute":  "3min",
    "5minute":  "5min",
    "10minute": "10min",
    "15minute": "15min",
    "20minute": "20min",
    "25minute": "25min",
}

# ── What interval to actually fetch from Upstox for each requested interval
# Resampled intervals all fetch as 1minute internally.
_FETCH_AS: dict[str, str] = {
    **{k: "1minute" for k in _RESAMPLE_MAP},
    **{k: k for k in _NATIVE_INTERVALS},
}

# ── Max date range per single API request (conservative, within Upstox limits)
# Keys are the interval we actually request from Upstox.
_CHUNK_SIZE: dict[str, relativedelta] = {
    "1minute":  relativedelta(months=1),
    "30minute": relativedelta(months=11),   # 1 year, kept slightly under
    "day":      relativedelta(months=11),
    "week":     relativedelta(years=9),
    "month":    relativedelta(years=9),
}

# Delay between consecutive API requests (seconds) to avoid rate limiting
_REQUEST_DELAY = 0.6


class HistoricalFetcher:
    """
    High-level helper that wraps :class:`UpstoxClient` and returns DataFrames.

    Key features
    ~~~~~~~~~~~~
    - Automatic chunking: large date ranges are split and stitched transparently.
    - Resampled intervals: 2/3/5/10/15/20/25-minute built from 1-min data.
    - VWAP: computed per trading day, appended as a column.
    - Progress logging: see how many chunks have been fetched.

    Example::

        fetcher = HistoricalFetcher()

        # 10 years of 5-min Nifty — ~120 API requests, handled automatically
        df = fetcher.fetch(
            instrument_key="NSE_INDEX|Nifty 50",
            interval="5minute",
            from_date="2015-01-01",
            to_date="2025-03-31",
        )
        print(df.shape)   # (187500, 8)
    """

    def __init__(
        self,
        client: UpstoxClient | None = None,
        request_delay: float = _REQUEST_DELAY,
    ) -> None:
        self._client = client or UpstoxClient()
        self._delay = request_delay

    # ── public ────────────────────────────────────────────────────────

    def fetch(
        self,
        instrument_key: str,
        interval: Interval | str,
        from_date: str,
        to_date: str,
        add_vwap: bool = True,
        nse_enrich: bool = False,
        symbol: str | None = None,
        series: str | None = None,
        on_chunk_progress: Callable[[int, int], None] | None = None,
        on_bhav_progress: Callable[[int, int], None] | None = None,
        on_merge_start: Callable[[], None] | None = None,
    ) -> pd.DataFrame:
        """
        Fetch historical candles for any date range, auto-chunking as needed.

        Parameters
        ----------
        instrument_key : str
            e.g. ``"NSE_INDEX|Nifty 50"``
        interval : str | Interval
            Any supported interval including 5minute, 15minute etc.
        from_date : str
            Start date ``YYYY-MM-DD``
        to_date : str
            End date   ``YYYY-MM-DD``
        add_vwap : bool
            Append a per-day cumulative VWAP column (default True).
        on_chunk_progress : callable, optional
            Called as on_chunk_progress(current, total) after each Upstox chunk.
        on_bhav_progress : callable, optional
            Forwarded to nse_enrichment.enrich().
        on_merge_start : callable, optional
            Forwarded to nse_enrichment.enrich().

        Returns
        -------
        pd.DataFrame
            Columns: timestamp, open, high, low, close, volume,
                     open_interest[, vwap]
            Sorted ascending by timestamp, duplicates removed.
        """
        interval_val = interval.value if isinstance(interval, Interval) else interval
        self._validate_interval(interval_val)

        fetch_as   = _FETCH_AS[interval_val]
        chunk_size = _CHUNK_SIZE[fetch_as]
        chunks     = list(self._date_chunks(from_date, to_date, chunk_size))
        total      = len(chunks)

        logger.info(
            "Fetching %s | %s | %s → %s  (%d chunk%s, fetch_as=%s)",
            instrument_key, interval_val, from_date, to_date,
            total, "s" if total != 1 else "", fetch_as,
        )

        frames: list[pd.DataFrame] = []
        for idx, (chunk_from, chunk_to) in enumerate(chunks, start=1):
            logger.info(
                "  Chunk %d/%d: %s → %s", idx, total,
                chunk_from.isoformat(), chunk_to.isoformat(),
            )
            raw = self._client.get_historical_candles(
                instrument_key=instrument_key,
                interval=fetch_as,
                from_date=chunk_from.isoformat(),
                to_date=chunk_to.isoformat(),
            )
            df_chunk = self._parse(raw, instrument_key)
            if not df_chunk.empty:
                frames.append(df_chunk)

            if on_chunk_progress:
                on_chunk_progress(idx, total)

            if idx < total:
                time.sleep(self._delay)

        if not frames:
            logger.warning("No data returned for %s in the requested range.", instrument_key)
            return pd.DataFrame(columns=_CANDLE_COLS)

        df = pd.concat(frames, ignore_index=True)
        df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
        logger.info("Total raw candles after concat: %d", len(df))

        # Resample if needed
        if interval_val in _RESAMPLE_MAP:
            rule = _RESAMPLE_MAP[interval_val]
            df = self._resample(df, rule)

        # VWAP
        if add_vwap and not df.empty:
            df = self._add_vwap(df)

        logger.info("Final candles: %d | columns: %s", len(df), list(df.columns))

        # ── NSE Enrichment (optional) ─────────────────────────────────────
        if nse_enrich:
            interval_val2 = interval.value if isinstance(interval, Interval) else interval
            _is_intraday = interval_val2 not in {"day", "week", "month"}
            df = enrich(
                df,
                instrument_key=instrument_key,
                symbol=symbol,
                series=series,
                is_intraday=_is_intraday,
                on_bhav_progress=on_bhav_progress,
                on_merge_start=on_merge_start,
            )
            # Rename timestamp → Date for final output
            if "timestamp" in df.columns:
                df = df.rename(columns={"timestamp": "Date"})

            # ── Rename columns to title case ──────────────────────────────────
        _col_rename = {
            "open": "Open",
            "close": "Close",
            "high": "High",
            "low": "Low",
            "vwap": "VWAP",
        }
        df = df.rename(columns={k: v for k, v in _col_rename.items() if k in df.columns})

        return df

    def fetch_intraday(
        self,
        instrument_key: str,
        interval: Interval | str = Interval.I1M,
        add_vwap: bool = True,
        nse_enrich: bool = False,
        symbol: str | None = None,
        series: str | None = None,
    ) -> pd.DataFrame:
        """Fetch today's intraday candles (no chunking needed — always today only)."""
        interval_val = interval.value if isinstance(interval, Interval) else interval

        if interval_val in _RESAMPLE_MAP:
            logger.info("Fetching intraday 1min → resampling to %s", interval_val)
            raw = self._client.get_intraday_candles(instrument_key, "1minute")
            df = self._parse(raw, instrument_key)
            if not df.empty:
                df = self._resample(df, _RESAMPLE_MAP[interval_val])
        else:
            logger.info("Fetching intraday %s | %s", instrument_key, interval_val)
            raw = self._client.get_intraday_candles(instrument_key, interval_val)
            df = self._parse(raw, instrument_key)

        if add_vwap and not df.empty:
            df = self._add_vwap(df)

        if nse_enrich and not df.empty:
            df = enrich(
                df,
                instrument_key=instrument_key,
                symbol=symbol,
                series=series,
                is_intraday=True,
            )
            if "timestamp" in df.columns:
                df = df.rename(columns={"timestamp": "Date"})

        return df

    def fetch_and_save(
        self,
        instrument_key: str,
        interval: Interval | str,
        from_date: str,
        to_date: str,
        out_dir: str | Path = "./data",
        fmt: str = "csv",
        add_vwap: bool = True,
        nse_enrich: bool = False,
        symbol: str | None = None,
        series: str | None = None,
        on_chunk_progress: Callable[[int, int], None] | None = None,
        on_bhav_progress: Callable[[int, int], None] | None = None,
        on_merge_start: Callable[[], None] | None = None,
    ) -> Path:
        """
        Fetch (with auto-chunking) and save to disk.

        Parameters
        ----------
        fmt : str
            ``"csv"`` or ``"parquet"`` (parquet recommended for large datasets)
        nse_enrich : bool
            If True, enrich with NSE Bhav Copy data before saving.
        symbol : str, optional
            NSE ticker (e.g. ``"RELIANCE"``). Needed when nse_enrich=True.
        series : str, optional
            NSE series (e.g. ``"EQ"``).
        """
        df = self.fetch(
            instrument_key, interval, from_date, to_date,
            add_vwap=add_vwap,
            nse_enrich=nse_enrich,
            symbol=symbol,
            series=series,
            on_chunk_progress=on_chunk_progress,
            on_bhav_progress=on_bhav_progress,
            on_merge_start=on_merge_start,
        )
        return self._save(df, instrument_key, interval, from_date, to_date, out_dir, fmt)

    # ── private: chunking ─────────────────────────────────────────────

    @staticmethod
    def _date_chunks(
        from_date: str,
        to_date: str,
        chunk_size: relativedelta,
    ) -> Iterator[tuple[date, date]]:
        """
        Yield (chunk_start, chunk_end) pairs that together cover
        [from_date, to_date] without exceeding chunk_size per pair.
        """
        start = date.fromisoformat(from_date)
        end   = date.fromisoformat(to_date)

        cursor = start
        while cursor <= end:
            chunk_end = min(cursor + chunk_size, end)
            yield cursor, chunk_end
            cursor = chunk_end + timedelta(days=1)

    # ── private: parsing ──────────────────────────────────────────────

    @staticmethod
    def _parse(raw: dict, instrument_key: str) -> pd.DataFrame:
        """Validate raw API response and return a sorted DataFrame."""
        resp    = HistoricalResponse.model_validate(raw)
        candles = resp.data.candles

        if not candles:
            logger.debug("Empty candle list for %s", instrument_key)
            return pd.DataFrame(columns=_CANDLE_COLS)

        records = [c.model_dump() for c in candles]
        df = pd.DataFrame(records)
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert("Asia/Kolkata")
        df = df.sort_values("timestamp").reset_index(drop=True)
        return df

    # ── private: resampling ───────────────────────────────────────────

    @staticmethod
    def _resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
        """
        Resample 1-min OHLCV to a higher timeframe using standard aggregation.
        Bars with no data (e.g. pre/post market) are dropped.
        """
        df = df.set_index("timestamp")
        resampled = df.resample(rule, label="left", closed="left").agg(
            open=("open",   "first"),
            high=("high",   "max"),
            low=("low",    "min"),
            close=("close",  "last"),
            volume=("volume", "sum"),
            open_interest=("open_interest", "last"),
        ).dropna(subset=["open"])
        resampled = resampled.reset_index()
        logger.info("Resampled → %d candles (%s)", len(resampled), rule)
        return resampled

    # ── private: VWAP ────────────────────────────────────────────────

    @staticmethod
    def _add_vwap(df: pd.DataFrame) -> pd.DataFrame:
        """
        Append per-day cumulative VWAP.

        Formula: VWAP = cumsum(typical_price × volume) / cumsum(volume)
        where typical_price = (high + low + close) / 3.
        Resets at midnight IST each trading day.
        """
        df = df.copy()
        df["_date"]   = df["timestamp"].dt.date
        typical       = (df["high"] + df["low"] + df["close"]) / 3
        df["_tp_vol"] = typical * df["volume"]

        df["_cum_tp_vol"] = df.groupby("_date")["_tp_vol"].cumsum()
        df["_cum_vol"]    = df.groupby("_date")["volume"].cumsum()
        df["vwap"]        = (df["_cum_tp_vol"] / df["_cum_vol"]).round(2)

        df = df.drop(columns=["_date", "_tp_vol", "_cum_tp_vol", "_cum_vol"])
        return df

    # ── private: saving ───────────────────────────────────────────────

    @staticmethod
    def _save(
        df: pd.DataFrame,
        instrument_key: str,
        interval: Interval | str,
        from_date: str,
        to_date: str,
        out_dir: str | Path,
        fmt: str,
    ) -> Path:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)

        interval_str = interval.value if isinstance(interval, Interval) else interval
        safe_key     = instrument_key.replace("|", "_").replace(" ", "_")
        stem         = f"{safe_key}_{interval_str}_{from_date}_{to_date}"

        if fmt == "parquet":
            path = out / f"{stem}.parquet"
            df.to_parquet(path, index=False)
        else:
            path = out / f"{stem}.csv"
            df.to_csv(path, index=False)

        logger.info("Saved → %s  (%d rows, %.1f MB)", path, len(df), path.stat().st_size / 1e6)
        return path

    # ── private: validation ───────────────────────────────────────────

    @staticmethod
    def _validate_interval(interval_val: str) -> None:
        all_supported = _NATIVE_INTERVALS | set(_RESAMPLE_MAP.keys())
        if interval_val not in all_supported:
            raise ValueError(
                f"Unsupported interval: {interval_val!r}.\n"
                f"Supported: {sorted(all_supported)}"
            )


# Module-level alias so CLI can import _date_chunks directly
def _date_chunks(from_date, to_date, chunk_size):
    yield from HistoricalFetcher._date_chunks(from_date, to_date, chunk_size)
