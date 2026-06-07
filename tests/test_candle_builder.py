"""
test_candle_builder.py — offline tests for the tick → OHLCV aggregator.

These tests use synthetic Tick objects — no live market or network needed.
They verify bucket alignment, OHLCV correctness, candle close emission,
multi-instrument isolation, and flush behaviour.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest

from upstox_historical.candle_builder import CandleBuilder, Candle, _PartialCandle
from upstox_historical.stream import Tick


_IST = timezone(timedelta(hours=5, minutes=30))


def _tick(key: str, ltp: float, volume: int = 0) -> Tick:
    """Build a minimal Tick for testing."""
    return Tick(instrument_key=key, mode="ltpc", ltp=ltp, volume=volume)


def _at(hour: int, minute: int, second: int = 0) -> datetime:
    """Construct an IST datetime on a fixed date for deterministic tests."""
    return datetime(2026, 6, 8, hour, minute, second, tzinfo=_IST)


# ── Construction / validation ─────────────────────────────────────────

def test_invalid_interval_raises():
    with pytest.raises(ValueError, match="interval_minutes must be one of"):
        CandleBuilder(interval_minutes=7)


def test_valid_intervals_accepted():
    for iv in (1, 3, 5, 10, 15, 30, 60):
        cb = CandleBuilder(interval_minutes=iv)
        assert cb.interval_minutes == iv


# ── Bucket alignment ──────────────────────────────────────────────────

def test_bucket_open_aligns_to_market_open_5m():
    cb = CandleBuilder(interval_minutes=5)
    # 09:17 should fall into the 09:15 bucket
    bucket = cb._get_bucket_open(_at(9, 17, 30))
    assert bucket == _at(9, 15, 0)


def test_bucket_open_aligns_to_market_open_15m():
    cb = CandleBuilder(interval_minutes=15)
    # 09:44 should fall into the 09:30 bucket
    bucket = cb._get_bucket_open(_at(9, 44))
    assert bucket == _at(9, 30, 0)


def test_bucket_open_exact_boundary():
    cb = CandleBuilder(interval_minutes=5)
    # 09:20:00 exactly should be its own bucket open
    bucket = cb._get_bucket_open(_at(9, 20, 0))
    assert bucket == _at(9, 20, 0)


def test_bucket_before_market_open_clamps():
    cb = CandleBuilder(interval_minutes=5)
    # 09:10 (before 09:15 open) should clamp to the open bucket
    bucket = cb._get_bucket_open(_at(9, 10))
    assert bucket == _at(9, 15, 0)


# ── OHLCV correctness within a single candle ──────────────────────────

def test_single_candle_ohlcv():
    closed: list[Candle] = []
    cb = CandleBuilder(interval_minutes=5, on_candle=closed.append)

    key = "NSE_INDEX|Nifty 50"
    # All ticks land in the 09:15 bucket
    with patch("upstox_historical.candle_builder.datetime") as mock_dt:
        mock_dt.now.return_value = _at(9, 16)
        cb.on_tick(_tick(key, 100.0, volume=10))   # open
        cb.on_tick(_tick(key, 105.0, volume=5))    # high
        cb.on_tick(_tick(key, 98.0, volume=5))     # low
        cb.on_tick(_tick(key, 102.0, volume=5))    # close

    # Still open — no closed candle yet
    assert closed == []

    current = cb.get_current(key)
    assert current is not None
    assert current.open == 100.0
    assert current.high == 105.0
    assert current.low == 98.0
    assert current.close == 102.0
    assert current.volume == 25
    assert current.tick_count == 4
    assert current.is_closed is False


def test_candle_closes_on_new_bucket():
    closed: list[Candle] = []
    cb = CandleBuilder(interval_minutes=5, on_candle=closed.append)
    key = "NSE_INDEX|Nifty 50"

    with patch("upstox_historical.candle_builder.datetime") as mock_dt:
        # First candle — 09:15 bucket
        mock_dt.now.return_value = _at(9, 16)
        cb.on_tick(_tick(key, 100.0, volume=10))
        cb.on_tick(_tick(key, 110.0, volume=10))

        # Second candle — 09:20 bucket → triggers close of first
        mock_dt.now.return_value = _at(9, 21)
        cb.on_tick(_tick(key, 111.0, volume=5))

    assert len(closed) == 1
    first = closed[0]
    assert first.open == 100.0
    assert first.high == 110.0
    assert first.low == 100.0
    assert first.close == 110.0
    assert first.volume == 20
    assert first.is_closed is True
    assert first.open_time == _at(9, 15, 0)
    assert first.close_time == _at(9, 20, 0)


# ── Multi-instrument isolation ────────────────────────────────────────

def test_multiple_instruments_isolated():
    cb = CandleBuilder(interval_minutes=5)
    nifty = "NSE_INDEX|Nifty 50"
    bank  = "NSE_INDEX|Nifty Bank"

    with patch("upstox_historical.candle_builder.datetime") as mock_dt:
        mock_dt.now.return_value = _at(9, 16)
        cb.on_tick(_tick(nifty, 24000.0))
        cb.on_tick(_tick(bank, 52000.0))
        cb.on_tick(_tick(nifty, 24050.0))

    nifty_c = cb.get_current(nifty)
    bank_c = cb.get_current(bank)

    assert nifty_c.close == 24050.0
    assert nifty_c.tick_count == 2
    assert bank_c.close == 52000.0
    assert bank_c.tick_count == 1
    assert set(cb.active_instruments) == {nifty, bank}


# ── Zero / invalid price handling ─────────────────────────────────────

def test_zero_ltp_skipped():
    cb = CandleBuilder(interval_minutes=5)
    key = "NSE_INDEX|Nifty 50"

    with patch("upstox_historical.candle_builder.datetime") as mock_dt:
        mock_dt.now.return_value = _at(9, 16)
        cb.on_tick(_tick(key, 0.0))     # should be skipped
        cb.on_tick(_tick(key, -5.0))    # should be skipped

    assert cb.get_current(key) is None
    assert cb.active_instruments == []


# ── Flush behaviour ───────────────────────────────────────────────────

def test_flush_emits_open_candles():
    closed: list[Candle] = []
    cb = CandleBuilder(interval_minutes=5, on_candle=closed.append)
    key = "NSE_INDEX|Nifty 50"

    with patch("upstox_historical.candle_builder.datetime") as mock_dt:
        mock_dt.now.return_value = _at(9, 16)
        cb.on_tick(_tick(key, 100.0, volume=10))
        cb.on_tick(_tick(key, 105.0, volume=10))

        # Flush before bucket would naturally close
        mock_dt.now.return_value = _at(9, 18)
        flushed = cb.flush()

    assert len(flushed) == 1
    assert flushed[0].close == 105.0
    assert flushed[0].is_closed is True
    assert flushed[0].volume == 20
    # on_candle callback also fired
    assert len(closed) == 1
    # State cleared after flush
    assert cb.active_instruments == []


def test_flush_with_no_ticks_is_empty():
    cb = CandleBuilder(interval_minutes=5)
    flushed = cb.flush()
    assert flushed == []


# ── History tracking ──────────────────────────────────────────────────

def test_history_accumulates():
    cb = CandleBuilder(interval_minutes=5)
    key = "NSE_INDEX|Nifty 50"

    with patch("upstox_historical.candle_builder.datetime") as mock_dt:
        mock_dt.now.return_value = _at(9, 16)
        cb.on_tick(_tick(key, 100.0))
        mock_dt.now.return_value = _at(9, 21)
        cb.on_tick(_tick(key, 110.0))   # closes 1st
        mock_dt.now.return_value = _at(9, 26)
        cb.on_tick(_tick(key, 120.0))   # closes 2nd

    history = cb.get_history(key)
    assert len(history) == 2
    assert history[0].close == 100.0
    assert history[1].close == 110.0
    assert cb.candle_count[key] == 2


# ── on_live_candle callback ───────────────────────────────────────────

def test_live_candle_callback_fires_every_tick():
    live_updates: list[Candle] = []
    cb = CandleBuilder(
        interval_minutes=5,
        on_live_candle=live_updates.append,
    )
    key = "NSE_INDEX|Nifty 50"

    with patch("upstox_historical.candle_builder.datetime") as mock_dt:
        mock_dt.now.return_value = _at(9, 16)
        cb.on_tick(_tick(key, 100.0))
        cb.on_tick(_tick(key, 101.0))
        cb.on_tick(_tick(key, 102.0))

    # One live update per tick
    assert len(live_updates) == 3
    assert all(c.is_closed is False for c in live_updates)
    assert live_updates[-1].close == 102.0


# ── Candle helper properties ──────────────────────────────────────────

def test_candle_properties():
    c = Candle(
        instrument_key="NSE_INDEX|Nifty 50",
        interval_minutes=5,
        open_time=_at(9, 15),
        close_time=_at(9, 20),
        open=100.0, high=110.0, low=95.0, close=105.0,
        volume=1000, tick_count=50, is_closed=True,
    )
    assert c.mid == 102.5          # (100 + 105) / 2
    assert c.range == 15.0         # 110 - 95
    d = c.to_dict()
    assert d["open"] == 100.0
    assert d["is_closed"] is True


def test_reset_clears_state():
    cb = CandleBuilder(interval_minutes=5)
    key = "NSE_INDEX|Nifty 50"

    with patch("upstox_historical.candle_builder.datetime") as mock_dt:
        mock_dt.now.return_value = _at(9, 16)
        cb.on_tick(_tick(key, 100.0))

    assert cb.get_current(key) is not None
    cb.reset(key)
    assert cb.get_current(key) is None
