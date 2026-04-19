"""
validation.py — Post-fetch data validation.

Runs a battery of sanity checks on fetched OHLCV data and returns a
structured ``ValidationReport``. Doesn't raise — reporting is informational,
because some issues (e.g. a zero-volume index candle) are acceptable and
the caller decides what to do.

Typical checks:

- **OHLC ordering**: high ≥ max(open, close) ≥ min(open, close) ≥ low ≥ 0
- **Non-negative volume**: volume ≥ 0
- **Monotonic timestamps**: no duplicates, strictly increasing
- **Gap detection**: expected trading days vs actual (uses NSE holiday
  calendar if the Bhav Copy cache is populated; otherwise Mon–Fri fallback).
- **Outlier detection**: candles with |log return| > N × rolling σ.
- **Zero volume**: flagged (common for index pre-open; informational).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from upstox_historical.cache import bhav_cache_path, bhav_cache_miss_marker

logger = logging.getLogger(__name__)


@dataclass
class ValidationReport:
    """Structured result of validating an OHLCV DataFrame."""

    n_rows: int = 0
    timestamp_col: str = "timestamp"

    # Hard errors — data is almost certainly broken
    errors: list[str] = field(default_factory=list)

    # Soft warnings — worth knowing but may be legitimate
    warnings: list[str] = field(default_factory=list)

    # Metric summaries
    ohlc_violations: int = 0
    negative_volume_rows: int = 0
    zero_volume_rows: int = 0
    duplicate_timestamps: int = 0
    non_monotonic: bool = False
    missing_trading_days: list[date] = field(default_factory=list)
    outlier_rows: int = 0

    def is_clean(self) -> bool:
        """True if no errors and no warnings."""
        return not self.errors and not self.warnings

    def summary(self) -> str:
        """Human-readable one-paragraph summary."""
        lines = [f"Validation report: {self.n_rows} rows"]
        if self.errors:
            lines.append(f"  ERRORS: {len(self.errors)}")
            for e in self.errors[:5]:
                lines.append(f"    • {e}")
            if len(self.errors) > 5:
                lines.append(f"    … and {len(self.errors) - 5} more")
        if self.warnings:
            lines.append(f"  warnings: {len(self.warnings)}")
            for w in self.warnings[:5]:
                lines.append(f"    • {w}")
            if len(self.warnings) > 5:
                lines.append(f"    … and {len(self.warnings) - 5} more")
        if not self.errors and not self.warnings:
            lines.append("  ✓ clean")
        return "\n".join(lines)


def validate(
    df: pd.DataFrame,
    *,
    timestamp_col: str = "auto",
    check_gaps: bool = True,
    outlier_sigma: float = 8.0,
    warn_zero_volume: bool = True,
) -> ValidationReport:
    """
    Run all checks on an OHLCV DataFrame.

    Parameters
    ----------
    df : pd.DataFrame
        Output of ``HistoricalFetcher.fetch()`` or ``fetch_many()``.
    timestamp_col : str
        "auto" picks whichever of ``timestamp`` / ``Date`` is present.
    check_gaps : bool
        Check for missing trading days (daily candles only).
    outlier_sigma : float
        Log-return outlier threshold (in rolling standard deviations).
    warn_zero_volume : bool
        Emit a warning for zero-volume candles.

    Returns
    -------
    ValidationReport
    """
    report = ValidationReport(n_rows=len(df))

    if df.empty:
        report.warnings.append("DataFrame is empty")
        return report

    # Resolve timestamp column
    if timestamp_col == "auto":
        if "timestamp" in df.columns:
            ts = "timestamp"
        elif "Date" in df.columns:
            ts = "Date"
        else:
            report.errors.append("No timestamp/Date column found")
            return report
    else:
        if timestamp_col not in df.columns:
            report.errors.append(f"Timestamp column {timestamp_col!r} missing")
            return report
        ts = timestamp_col
    report.timestamp_col = ts

    # ── OHLC ordering ────────────────────────────────────────────────
    required = {"Open", "High", "Low", "Close"}
    missing_cols = required - set(df.columns)
    if missing_cols:
        report.errors.append(f"Missing OHLC columns: {sorted(missing_cols)}")
    else:
        o, h, l, c = df["Open"], df["High"], df["Low"], df["Close"]
        bad = ~(
            (h >= o)
            & (h >= c)
            & (l <= o)
            & (l <= c)
            & (l >= 0)
        )
        violations = int(bad.sum())
        report.ohlc_violations = violations
        if violations > 0:
            report.errors.append(
                f"OHLC ordering violated in {violations} row(s) "
                f"(first at index {int(bad.idxmax())})"
            )

    # ── Volume sanity ────────────────────────────────────────────────
    if "Volume" in df.columns:
        negatives = int((df["Volume"] < 0).sum())
        zeros = int((df["Volume"] == 0).sum())
        report.negative_volume_rows = negatives
        report.zero_volume_rows = zeros
        if negatives > 0:
            report.errors.append(f"{negatives} row(s) with negative volume")
        if zeros > 0 and warn_zero_volume:
            report.warnings.append(
                f"{zeros} row(s) with zero volume (ok for indices pre-open, "
                f"suspicious for equities)"
            )

    # ── Timestamp integrity ──────────────────────────────────────────
    timestamps = pd.to_datetime(df[ts], errors="coerce", utc=True)
    nat_count = int(timestamps.isna().sum())
    if nat_count > 0:
        report.errors.append(f"{nat_count} unparseable timestamp(s)")

    dups = int(df[ts].duplicated().sum())
    report.duplicate_timestamps = dups
    if dups > 0:
        report.errors.append(f"{dups} duplicate timestamp(s)")

    # Monotonic (after dropping NaT)
    if nat_count == 0:
        if not timestamps.is_monotonic_increasing:
            report.non_monotonic = True
            report.errors.append("Timestamps are not strictly increasing")

    # ── Gap detection (daily candles only) ───────────────────────────
    if check_gaps and "timestamp" in df.columns or ts == "Date":
        daily = _looks_daily(df, ts)
        if daily:
            present_dates = set(pd.to_datetime(df[ts]).dt.date)
            if present_dates:
                expected = _expected_trading_days(min(present_dates), max(present_dates))
                missing = sorted(expected - present_dates)
                report.missing_trading_days = missing
                if missing:
                    report.warnings.append(
                        f"{len(missing)} expected trading day(s) missing "
                        f"(e.g. {missing[0]} … {missing[-1] if len(missing) > 1 else ''})"
                    )

    # ── Outlier detection on log returns ─────────────────────────────
    if "Close" in df.columns and len(df) >= 30:
        close = df["Close"].astype(float)
        log_ret = np.log(close / close.shift(1))
        rolling_std = log_ret.rolling(window=30, min_periods=10).std()
        with np.errstate(invalid="ignore"):
            z = (log_ret - log_ret.rolling(30, min_periods=10).mean()) / rolling_std
        outliers = int((z.abs() > outlier_sigma).sum())
        report.outlier_rows = outliers
        if outliers > 0:
            report.warnings.append(
                f"{outliers} candle(s) with |log return z-score| > {outlier_sigma} "
                f"(possible data errors or genuine shocks)"
            )

    return report


# ── gap detection helpers ────────────────────────────────────────────

def _looks_daily(df: pd.DataFrame, ts_col: str) -> bool:
    """Heuristic: is this a daily (or lower-frequency) series?"""
    if len(df) < 2:
        return False
    ts = pd.to_datetime(df[ts_col])
    # Median delta — if it's ≥ ~20 hours, treat as daily-or-coarser
    delta = ts.diff().dropna().median()
    if pd.isna(delta):
        return False
    return delta >= pd.Timedelta(hours=20)


def _expected_trading_days(start: date, end: date) -> set[date]:
    """
    Return set of expected trading days between start and end (inclusive).

    Prefers the Bhav Copy cache (authoritative NSE trading calendar).
    Falls back to Mon–Fri (crude but better than nothing) if the cache
    is sparse for this range.
    """
    # First try the Bhav cache — anywhere we have a parquet it WAS a trading day,
    # anywhere we have a miss marker it was NOT.
    have_parquet: set[date] = set()
    have_miss: set[date] = set()
    d = start
    while d <= end:
        try:
            if bhav_cache_path(d).exists():
                have_parquet.add(d)
            elif bhav_cache_miss_marker(d).exists():
                have_miss.add(d)
        except OSError:
            pass
        d += timedelta(days=1)

    # Coverage: parquet OR miss == "we checked"
    total_days = (end - start).days + 1
    checked = len(have_parquet) + len(have_miss)

    if checked / max(total_days, 1) >= 0.8:
        # Authoritative answer
        return have_parquet

    # Fallback: Mon–Fri (ignores Indian holidays; warnings will include
    # festivals but that's better than missing real gaps entirely)
    out: set[date] = set()
    d = start
    while d <= end:
        if d.weekday() < 5:  # 0=Mon .. 4=Fri
            out.add(d)
        d += timedelta(days=1)
    return out


# ── repair utilities ─────────────────────────────────────────────────

def repair(df: pd.DataFrame, *, drop_duplicates: bool = True, sort: bool = True) -> pd.DataFrame:
    """
    Best-effort cleanup of a candle DataFrame.

    - Drops rows with NaT timestamps.
    - Drops duplicate timestamps (keeping first occurrence).
    - Sorts by timestamp.
    - Drops rows violating OHLC ordering (returns the clean subset).

    Original row count vs returned count lets callers know what was removed.
    """
    if df.empty:
        return df
    df = df.copy()
    ts = "timestamp" if "timestamp" in df.columns else "Date"

    df[ts] = pd.to_datetime(df[ts], errors="coerce", utc=True).dt.tz_convert("Asia/Kolkata")
    df = df.dropna(subset=[ts])

    if drop_duplicates:
        df = df.drop_duplicates(subset=[ts], keep="first")
    if sort:
        df = df.sort_values(ts).reset_index(drop=True)

    if {"Open", "High", "Low", "Close"}.issubset(df.columns):
        o, h, l, c = df["Open"], df["High"], df["Low"], df["Close"]
        ok = (h >= o) & (h >= c) & (l <= o) & (l <= c) & (l >= 0)
        df = df.loc[ok].reset_index(drop=True)

    return df
