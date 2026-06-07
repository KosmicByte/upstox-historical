"""
candle_builder.py — Aggregate live ticks from stream.py into OHLCV candles.

Takes Tick objects from LiveStream and builds time-bucketed candles
(1m, 5m, 15m, etc.) in memory. Emits a completed candle via callback
when the candle period closes.

This is the bridge between raw tick data and model-consumable OHLCV bars.

Usage::

    from upstox_historical.stream import LiveStream
    from upstox_historical.candle_builder import CandleBuilder, Candle

    builder = CandleBuilder(
        interval_minutes=5,
        on_candle=lambda c: print(c),
    )

    stream = LiveStream(
        instrument_keys=["NSE_INDEX|Nifty 50"],
        mode="ltpc",
        on_tick=builder.on_tick,
    )

    asyncio.run(stream.connect())
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Callable

from upstox_historical.stream import Tick

logger = logging.getLogger(__name__)

# IST offset
_IST = timezone(timedelta(hours=5, minutes=30))

# Valid candle intervals in minutes
_VALID_INTERVALS = {1, 3, 5, 10, 15, 30, 60}


# ── Data types ────────────────────────────────────────────────────────

@dataclass
class Candle:
    """
    A completed OHLCV candle for a single instrument and time bucket.

    Attributes
    ----------
    instrument_key : str
        e.g. ``"NSE_INDEX|Nifty 50"``
    interval_minutes : int
        Candle duration in minutes.
    open_time : datetime
        Candle open timestamp (IST, tz-aware).
    close_time : datetime
        Candle close timestamp (IST, tz-aware).
    open : float
    high : float
    low  : float
    close : float
        Last traded price at candle close.
    volume : int
        Total volume traded during the candle.
    tick_count : int
        Number of ticks that contributed to this candle.
    is_closed : bool
        Always True for candles emitted by CandleBuilder.
        Partial/live candles have is_closed=False.
    """
    instrument_key: str
    interval_minutes: int
    open_time: datetime
    close_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int = 0
    tick_count: int = 0
    is_closed: bool = False

    @property
    def mid(self) -> float:
        """Midpoint price (open + close) / 2."""
        return round((self.open + self.close) / 2, 4)

    @property
    def range(self) -> float:
        """High - Low range."""
        return round(self.high - self.low, 4)

    def to_dict(self) -> dict:
        """Convert to plain dict for DataFrame construction."""
        return {
            "instrument_key": self.instrument_key,
            "interval_minutes": self.interval_minutes,
            "open_time": self.open_time,
            "close_time": self.close_time,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "tick_count": self.tick_count,
            "is_closed": self.is_closed,
        }


@dataclass
class _PartialCandle:
    """Internal mutable candle being built from ticks."""
    instrument_key: str
    interval_minutes: int
    open_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int = 0
    tick_count: int = 0

    def update(self, ltp: float, volume: int = 0) -> None:
        self.high = max(self.high, ltp)
        self.low = min(self.low, ltp)
        self.close = ltp
        self.volume += volume
        self.tick_count += 1

    def to_candle(self, close_time: datetime, is_closed: bool = True) -> Candle:
        return Candle(
            instrument_key=self.instrument_key,
            interval_minutes=self.interval_minutes,
            open_time=self.open_time,
            close_time=close_time,
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            volume=self.volume,
            tick_count=self.tick_count,
            is_closed=is_closed,
        )


# ── CandleBuilder ─────────────────────────────────────────────────────

OnCandleCallback = Callable[[Candle], None]


class CandleBuilder:
    """
    Aggregates live ticks into OHLCV candles.

    One CandleBuilder instance handles multiple instruments simultaneously.
    Each instrument gets its own independent candle state.

    Parameters
    ----------
    interval_minutes : int
        Candle duration. Must be one of: 1, 3, 5, 10, 15, 30, 60.
    on_candle : callable
        Called with a completed ``Candle`` when a period closes.
        Receives is_closed=True candles only.
    on_live_candle : callable, optional
        Called with every tick update as a partial candle (is_closed=False).
        Useful for live dashboards. Not called if None.
    market_open : str
        Market open time in HH:MM (IST). Default: ``"09:15"``.
    market_close : str
        Market close time in HH:MM (IST). Default: ``"15:30"``.

    Example
    -------
    ::

        builder = CandleBuilder(
            interval_minutes=5,
            on_candle=lambda c: print(f"Closed: {c.instrument_key} {c.close}"),
        )

        # Pass builder.on_tick directly to LiveStream
        stream = LiveStream(
            instrument_keys=["NSE_INDEX|Nifty 50"],
            on_tick=builder.on_tick,
        )
    """

    def __init__(
        self,
        interval_minutes: int = 5,
        on_candle: OnCandleCallback | None = None,
        on_live_candle: OnCandleCallback | None = None,
        market_open: str = "09:15",
        market_close: str = "15:30",
    ) -> None:
        if interval_minutes not in _VALID_INTERVALS:
            raise ValueError(
                f"interval_minutes must be one of {sorted(_VALID_INTERVALS)}. "
                f"Got: {interval_minutes}"
            )

        self.interval_minutes = interval_minutes
        self._on_candle = on_candle
        self._on_live_candle = on_live_candle

        # Parse market hours
        open_h, open_m = map(int, market_open.split(":"))
        close_h, close_m = map(int, market_close.split(":"))
        self._market_open_minutes  = open_h * 60 + open_m    # 555 for 09:15
        self._market_close_minutes = close_h * 60 + close_m  # 930 for 15:30

        # instrument_key → _PartialCandle
        self._partials: dict[str, _PartialCandle] = {}

        # Completed candles buffer (in-memory history for this session)
        self._history: dict[str, list[Candle]] = defaultdict(list)

        logger.info(
            "CandleBuilder initialised: interval=%dm market=%s–%s IST",
            interval_minutes, market_open, market_close,
        )

    # ── Core tick handler ─────────────────────────────────────────────

    def on_tick(self, tick: Tick) -> None:
        """
        Process a single Tick from LiveStream.

        Pass this method directly to LiveStream as the on_tick callback::

            stream = LiveStream(..., on_tick=builder.on_tick)

        Parameters
        ----------
        tick : Tick
            Decoded tick from the WebSocket feed.
        """
        if tick.ltp <= 0:
            return  # skip zero/invalid prices

        now_ist = datetime.now(_IST)
        bucket_open = self._get_bucket_open(now_ist)
        key = tick.instrument_key

        partial = self._partials.get(key)

        if partial is None:
            # First tick for this instrument — open a new candle
            self._partials[key] = _PartialCandle(
                instrument_key=key,
                interval_minutes=self.interval_minutes,
                open_time=bucket_open,
                open=tick.ltp,
                high=tick.ltp,
                low=tick.ltp,
                close=tick.ltp,
                volume=tick.volume,
                tick_count=1,
            )
            logger.debug("New candle opened: %s @ %s ltp=%.2f", key, bucket_open, tick.ltp)

        elif partial.open_time == bucket_open:
            # Same bucket — update existing candle
            partial.update(tick.ltp, tick.volume)

        else:
            # New bucket — close the current candle and open a fresh one
            close_time = partial.open_time + timedelta(minutes=self.interval_minutes)
            closed = partial.to_candle(close_time=close_time, is_closed=True)
            self._history[key].append(closed)

            logger.debug(
                "Candle closed: %s [%s–%s] O=%.2f H=%.2f L=%.2f C=%.2f V=%d",
                key, closed.open_time.strftime("%H:%M"),
                close_time.strftime("%H:%M"),
                closed.open, closed.high, closed.low, closed.close, closed.volume,
            )

            if self._on_candle:
                try:
                    self._on_candle(closed)
                except Exception as exc:
                    logger.error("on_candle callback raised: %s", exc)

            # Open new candle with this tick
            self._partials[key] = _PartialCandle(
                instrument_key=key,
                interval_minutes=self.interval_minutes,
                open_time=bucket_open,
                open=tick.ltp,
                high=tick.ltp,
                low=tick.ltp,
                close=tick.ltp,
                volume=tick.volume,
                tick_count=1,
            )

        # Emit live (partial) candle if callback registered
        if self._on_live_candle:
            live = self._partials[key].to_candle(
                close_time=bucket_open + timedelta(minutes=self.interval_minutes),
                is_closed=False,
            )
            try:
                self._on_live_candle(live)
            except Exception as exc:
                logger.error("on_live_candle callback raised: %s", exc)

    # ── Session close ─────────────────────────────────────────────────

    def flush(self) -> list[Candle]:
        """
        Close and emit all open partial candles.

        Call this at market close (15:30 IST) or on stream disconnect
        to ensure the last candle of the session is not lost.

        Returns
        -------
        list[Candle]
            All flushed candles (is_closed=True).
        """
        flushed: list[Candle] = []
        now_ist = datetime.now(_IST)

        for key, partial in list(self._partials.items()):
            if partial.tick_count == 0:
                continue
            close_time = now_ist
            closed = partial.to_candle(close_time=close_time, is_closed=True)
            self._history[key].append(closed)
            flushed.append(closed)

            if self._on_candle:
                try:
                    self._on_candle(closed)
                except Exception as exc:
                    logger.error("on_candle flush callback raised: %s", exc)

        self._partials.clear()
        logger.info("CandleBuilder flushed %d open candles.", len(flushed))
        return flushed

    # ── History access ────────────────────────────────────────────────

    def get_history(self, instrument_key: str) -> list[Candle]:
        """
        Return all completed candles for an instrument in this session.

        Parameters
        ----------
        instrument_key : str

        Returns
        -------
        list[Candle]
            Sorted oldest-first.
        """
        return list(self._history.get(instrument_key, []))

    def get_current(self, instrument_key: str) -> Candle | None:
        """
        Return the current (open, not yet closed) candle for an instrument.

        Returns None if no ticks have been received yet.
        """
        partial = self._partials.get(instrument_key)
        if partial is None:
            return None
        now_ist = datetime.now(_IST)
        close_time = partial.open_time + timedelta(minutes=self.interval_minutes)
        return partial.to_candle(close_time=close_time, is_closed=False)

    def reset(self, instrument_key: str | None = None) -> None:
        """
        Clear state for one instrument or all instruments.

        Parameters
        ----------
        instrument_key : str, optional
            If None, clears all instruments.
        """
        if instrument_key:
            self._partials.pop(instrument_key, None)
            self._history.pop(instrument_key, None)
        else:
            self._partials.clear()
            self._history.clear()
        logger.info("CandleBuilder reset: %s", instrument_key or "ALL")

    # ── Internal helpers ──────────────────────────────────────────────

    def _get_bucket_open(self, now_ist: datetime) -> datetime:
        """
        Calculate the open timestamp of the current candle bucket.

        Aligns to market open (09:15 IST) so candles always start on
        clean boundaries: 09:15, 09:20, 09:25, ... for a 5m interval.
        """
        minutes_since_midnight = now_ist.hour * 60 + now_ist.minute
        minutes_since_open = minutes_since_midnight - self._market_open_minutes

        if minutes_since_open < 0:
            # Before market open — treat as first bucket
            minutes_since_open = 0

        bucket_offset = (minutes_since_open // self.interval_minutes) * self.interval_minutes
        bucket_minutes = self._market_open_minutes + bucket_offset

        bucket_h = bucket_minutes // 60
        bucket_m = bucket_minutes % 60

        return now_ist.replace(
            hour=bucket_h,
            minute=bucket_m,
            second=0,
            microsecond=0,
        )

    @property
    def active_instruments(self) -> list[str]:
        """List of instruments with open partial candles."""
        return list(self._partials.keys())

    @property
    def candle_count(self) -> dict[str, int]:
        """Number of completed candles per instrument this session."""
        return {k: len(v) for k, v in self._history.items()}
