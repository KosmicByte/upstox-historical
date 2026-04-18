"""
async_fetcher.py — Async, concurrent, checkpoint-resumable historical fetcher.

Key improvements over the sync ``HistoricalFetcher``:

- **Concurrency**: uses ``asyncio.gather`` with an internal semaphore in the
  client, so multiple chunks are in flight simultaneously while still
  respecting Upstox rate limits.
- **Resume on failure**: every completed chunk is written to a per-job
  checkpoint directory immediately. If the process crashes, the next run
  picks up where it left off — no wasted API calls.
- **Tenacity retries**: transient HTTP errors are retried with exponential
  backoff (configured in ``AsyncUpstoxClient``).
- **Progress callback**: optional per-chunk callback so callers (CLI,
  Jupyter) can render progress bars without coupling the core logic to Rich.

Interface mirrors the sync fetcher where possible; everything is async.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Awaitable, Callable, Iterator, Optional

import pandas as pd
from dateutil.relativedelta import relativedelta

from upstox_historical.async_client import AsyncUpstoxClient
from upstox_historical.cache import (
    checkpoint_clear,
    checkpoint_dir,
    checkpoint_load,
    checkpoint_store,
)
from upstox_historical.fetcher import (
    _CANDLE_COLS,
    _CHUNK_SIZE,
    _FETCH_AS,
    _NATIVE_INTERVALS,
    _RESAMPLE_MAP,
    HistoricalFetcher,
)
from upstox_historical.models import HistoricalResponse, Interval
from upstox_historical.nse_enrichment import enrich

logger = logging.getLogger(__name__)

# Type alias for progress callback.
# Called as: progress_cb(completed_chunks, total_chunks, chunk_from, chunk_to)
ProgressCB = Callable[[int, int, date, date], None]


class AsyncHistoricalFetcher:
    """
    Async historical fetcher with concurrency, checkpointing, and retries.

    Example::

        import asyncio
        from upstox_historical.async_fetcher import AsyncHistoricalFetcher

        async def main():
            fetcher = AsyncHistoricalFetcher()
            df = await fetcher.fetch(
                "NSE_INDEX|Nifty 50", "5minute",
                "2015-01-01", "2025-03-31",
            )
            print(df.shape)

        asyncio.run(main())
    """

    def __init__(
        self,
        client: AsyncUpstoxClient | None = None,
        use_checkpoints: bool = True,
    ) -> None:
        self._client = client or AsyncUpstoxClient()
        self._use_checkpoints = use_checkpoints
        self._owns_client = client is None

    # ── public ────────────────────────────────────────────────────────

    async def fetch(
        self,
        instrument_key: str,
        interval: Interval | str,
        from_date: str,
        to_date: str,
        add_vwap: bool = True,
        nse_enrich: bool = False,
        symbol: str | None = None,
        series: str | None = None,
        progress_cb: Optional[ProgressCB] = None,
        enrich_progress_cb: Optional[Callable[[int, int, date], None]] = None,
    ) -> pd.DataFrame:
        """
        Fetch historical candles concurrently with checkpointing.

        Parameters
        ----------
        instrument_key : str
        interval : Interval | str
        from_date, to_date : str
            YYYY-MM-DD inclusive.
        add_vwap : bool
            Append per-day cumulative VWAP.
        nse_enrich : bool
            Merge NSE Bhav Copy columns (Prev Close, Trades, Deliverable, etc.).
        symbol, series : str, optional
            NSE ticker + series for enrichment.
        progress_cb : callable, optional
            Invoked as ``progress_cb(done, total, chunk_from, chunk_to)`` after
            each chunk completes. Used by the CLI for Rich progress bars.

        Returns
        -------
        pd.DataFrame
        """
        interval_val = interval.value if isinstance(interval, Interval) else interval
        _validate_interval(interval_val)

        fetch_as = _FETCH_AS[interval_val]
        chunk_size = _CHUNK_SIZE[fetch_as]
        chunks = list(_date_chunks(from_date, to_date, chunk_size))
        total = len(chunks)

        logger.info(
            "[async] Fetching %s | %s | %s → %s  (%d chunks, fetch_as=%s)",
            instrument_key, interval_val, from_date, to_date, total, fetch_as,
        )

        ckpt = checkpoint_dir(instrument_key, fetch_as, from_date, to_date) \
            if self._use_checkpoints else None

        # Track how many are already on disk for progress accounting
        prefetched = 0
        if ckpt is not None:
            for idx in range(1, total + 1):
                if checkpoint_load(ckpt, idx) is not None:
                    prefetched += 1
            if prefetched:
                logger.info(
                    "[async] Resume: %d/%d chunks already cached on disk",
                    prefetched, total,
                )

        # Shared completion counter for progress callback
        state = {"done": prefetched}
        lock = asyncio.Lock()

        # Use our own client if we own it (context-manage it), else assume caller does
        if self._owns_client:
            await self._client.__aenter__()

        try:
            tasks = [
                self._fetch_one_chunk(
                    instrument_key=instrument_key,
                    fetch_as=fetch_as,
                    chunk_idx=idx,
                    chunk_from=cf,
                    chunk_to=ct,
                    ckpt_dir=ckpt,
                    total=total,
                    state=state,
                    lock=lock,
                    progress_cb=progress_cb,
                )
                for idx, (cf, ct) in enumerate(chunks, start=1)
            ]
            chunk_results = await asyncio.gather(*tasks, return_exceptions=False)
        finally:
            if self._owns_client:
                await self._client.aclose()

        frames = [df for df in chunk_results if df is not None and not df.empty]

        if not frames:
            logger.warning("No data returned for %s in requested range.", instrument_key)
            return pd.DataFrame(columns=_CANDLE_COLS)

        df = (
            pd.concat(frames, ignore_index=True)
            .drop_duplicates(subset=["timestamp"])
            .sort_values("timestamp")
            .reset_index(drop=True)
        )
        logger.info("[async] Total raw candles after concat: %d", len(df))

        # Resample higher-level intervals from 1-min base
        if interval_val in _RESAMPLE_MAP:
            df = HistoricalFetcher._resample(df, _RESAMPLE_MAP[interval_val])

        # Per-day cumulative VWAP
        if add_vwap and not df.empty:
            df = HistoricalFetcher._add_vwap(df)

        logger.info("[async] Final candles: %d | columns: %s", len(df), list(df.columns))

        # NSE enrichment (sync — uses its own on-disk cache, I/O-bound but
        # well-cached so the overhead is small)
        if nse_enrich:
            is_intraday = interval_val not in {"day", "week", "month"}
            df = enrich(
                df,
                instrument_key=instrument_key,
                symbol=symbol,
                series=series,
                is_intraday=is_intraday,
                progress_cb=enrich_progress_cb,
            )
            if "timestamp" in df.columns:
                df = df.rename(columns={"timestamp": "Date"})

        # Clean up checkpoints only on fully-successful completion
        if ckpt is not None:
            checkpoint_clear(ckpt)
            logger.debug("[async] Cleared checkpoints under %s", ckpt)

        return df

    async def fetch_many(
        self,
        instrument_keys: list[str],
        interval: Interval | str,
        from_date: str,
        to_date: str,
        *,
        add_vwap: bool = True,
        nse_enrich: bool = False,
        symbols: dict[str, str] | None = None,
        series: str | None = None,
        progress_cb: Optional[Callable[[str, int, int], None]] = None,
    ) -> dict[str, pd.DataFrame]:
        """
        Fetch many instruments concurrently (through the same rate limiter).

        Parameters
        ----------
        instrument_keys : list[str]
            Instruments to fetch.
        symbols : dict[str, str], optional
            Map from instrument_key → NSE ticker symbol (used for enrichment).
            If ``nse_enrich=True`` and an instrument is missing, its ticker is
            derived heuristically from the instrument_key.
        progress_cb : callable, optional
            ``progress_cb(instrument_key, done_instruments, total_instruments)``

        Returns
        -------
        dict[str, pd.DataFrame]
            Keyed by instrument_key. Empty DataFrames for failed fetches are
            still included so the caller can distinguish "no data" from "no attempt".
        """
        symbols = symbols or {}
        total = len(instrument_keys)

        # Use a shared client so rate limits span all instruments correctly
        if self._owns_client:
            await self._client.__aenter__()

        results: dict[str, pd.DataFrame] = {}
        done = 0

        try:
            async def _one(ikey: str) -> tuple[str, pd.DataFrame]:
                nonlocal done
                sub = AsyncHistoricalFetcher(
                    client=self._client, use_checkpoints=self._use_checkpoints,
                )
                # Mark sub so it won't close the shared client
                sub._owns_client = False
                try:
                    df = await sub.fetch(
                        ikey, interval, from_date, to_date,
                        add_vwap=add_vwap,
                        nse_enrich=nse_enrich,
                        symbol=symbols.get(ikey),
                        series=series,
                    )
                except Exception as exc:
                    logger.error("Fetch failed for %s: %s", ikey, exc)
                    df = pd.DataFrame(columns=_CANDLE_COLS)

                done += 1
                if progress_cb is not None:
                    progress_cb(ikey, done, total)
                return ikey, df

            paired = await asyncio.gather(*[_one(k) for k in instrument_keys])
            results = dict(paired)
        finally:
            if self._owns_client:
                await self._client.aclose()

        return results

    # ── internals ─────────────────────────────────────────────────────

    async def _fetch_one_chunk(
        self,
        *,
        instrument_key: str,
        fetch_as: str,
        chunk_idx: int,
        chunk_from: date,
        chunk_to: date,
        ckpt_dir: Optional[Path],
        total: int,
        state: dict,
        lock: asyncio.Lock,
        progress_cb: Optional[ProgressCB],
    ) -> Optional[pd.DataFrame]:
        """Fetch a single chunk, using checkpoint cache if available."""
        # Try cache first
        if ckpt_dir is not None:
            cached = checkpoint_load(ckpt_dir, chunk_idx)
            if cached is not None:
                logger.debug(
                    "  [chunk %d/%d] RESUME %s → %s (cached)",
                    chunk_idx, total, chunk_from, chunk_to,
                )
                # count already in `prefetched` accounting
                if progress_cb is not None:
                    async with lock:
                        progress_cb(state["done"], total, chunk_from, chunk_to)
                return cached

        logger.debug(
            "  [chunk %d/%d] FETCH %s → %s",
            chunk_idx, total, chunk_from, chunk_to,
        )
        raw = await self._client.get_historical_candles(
            instrument_key=instrument_key,
            interval=fetch_as,
            from_date=chunk_from.isoformat(),
            to_date=chunk_to.isoformat(),
        )
        df_chunk = _parse_candles(raw, instrument_key)

        if ckpt_dir is not None and not df_chunk.empty:
            checkpoint_store(ckpt_dir, chunk_idx, df_chunk)

        async with lock:
            state["done"] += 1
            done = state["done"]

        if progress_cb is not None:
            progress_cb(done, total, chunk_from, chunk_to)

        return df_chunk


# ── module-level helpers (shared with sync fetcher) ──────────────────

def _parse_candles(raw: dict, instrument_key: str) -> pd.DataFrame:
    """Validate Upstox response and return a sorted DataFrame."""
    resp = HistoricalResponse.model_validate(raw)
    candles = resp.data.candles
    if not candles:
        return pd.DataFrame(columns=_CANDLE_COLS)
    df = pd.DataFrame([c.model_dump() for c in candles])
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert("Asia/Kolkata")
    df = df.sort_values("timestamp").reset_index(drop=True)
    return df


def _date_chunks(
    from_date: str,
    to_date: str,
    chunk_size: relativedelta,
) -> Iterator[tuple[date, date]]:
    """Yield (start, end) pairs covering [from_date, to_date]."""
    start = date.fromisoformat(from_date)
    end = date.fromisoformat(to_date)
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + chunk_size, end)
        yield cursor, chunk_end
        cursor = chunk_end + timedelta(days=1)


def _validate_interval(interval_val: str) -> None:
    all_supported = _NATIVE_INTERVALS | set(_RESAMPLE_MAP.keys())
    if interval_val not in all_supported:
        raise ValueError(
            f"Unsupported interval: {interval_val!r}. "
            f"Supported: {sorted(all_supported)}"
        )
