"""
models.py — Pydantic v2 models that mirror the Upstox API v2 response shapes.
"""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, field_validator, model_validator


# ──────────────────────────────────────────────
# Enums matching Upstox API accepted values
# ──────────────────────────────────────────────

class Interval(str, Enum):
    """
    Candle intervals.
    Native Upstox: 1minute, 30minute, day, week, month.
    Resampled from 1min: 2minute, 3minute, 5minute, 10minute, 15minute, 20minute, 25minute.
    """
    I1M  = "1minute"
    I2M  = "2minute"
    I3M  = "3minute"
    I5M  = "5minute"
    I10M = "10minute"
    I15M = "15minute"
    I20M = "20minute"
    I25M = "25minute"
    I30M = "30minute"
    D1   = "day"
    W1   = "week"
    M1   = "month"


class InstrumentType(str, Enum):
    EQ     = "EQ"
    FUT    = "FUT"
    OPT    = "OPT"
    INDEX  = "INDEX"


# ──────────────────────────────────────────────
# OHLCV candle
# ──────────────────────────────────────────────

class Candle(BaseModel):
    """
    One OHLCV row from Upstox.
    Upstox returns candles as plain lists:
        [timestamp, open, high, low, close, volume, open_interest]
    This model accepts BOTH list and dict formats.
    """
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    open_interest: int = 0

    @model_validator(mode="before")
    @classmethod
    def handle_list_input(cls, v: Any) -> Any:
        """Convert list → dict so pydantic can validate field by field."""
        if isinstance(v, (list, tuple)):
            keys = ["timestamp", "open", "high", "low", "close", "volume", "open_interest"]
            return dict(zip(keys, v))
        return v

    @field_validator("open", "high", "low", "close", mode="before")
    @classmethod
    def coerce_float(cls, v: Any) -> float:
        return float(v)

    @field_validator("volume", "open_interest", mode="before")
    @classmethod
    def coerce_int(cls, v: Any) -> int:
        return int(v)


# ──────────────────────────────────────────────
# API wrapper models
# ──────────────────────────────────────────────

class CandleData(BaseModel):
    candles: list[Candle]


class HistoricalResponse(BaseModel):
    status: str
    data: CandleData

    @field_validator("status")
    @classmethod
    def check_success(cls, v: str) -> str:
        if v != "success":
            raise ValueError(f"Upstox API returned status={v!r} (expected 'success')")
        return v


# ──────────────────────────────────────────────
# Instrument search models
# ──────────────────────────────────────────────

class InstrumentSearchResult(BaseModel):
    """Minimal shape of one instrument from the Upstox search API."""
    instrument_key: str
    trading_symbol: str
    name: str
    exchange: str
    instrument_type: str
    expiry: str | None = None
    strike_price: float | None = None
    option_type: str | None = None
    lot_size: int | None = None
