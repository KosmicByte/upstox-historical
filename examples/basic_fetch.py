#!/usr/bin/env python3
"""
examples/basic_fetch.py
────────────────────────
Demonstrates programmatic usage of HistoricalFetcher without the CLI.

Run:
    uv run python examples/basic_fetch.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# ── make sure src/ is on the path when running as a script ──────────
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from upstox_historical.fetcher import HistoricalFetcher
from upstox_historical.instruments import NSE
from upstox_historical.models import Interval
from upstox_historical.config import configure_logging

configure_logging("INFO")


def main() -> None:
    fetcher = HistoricalFetcher()   # reads UPSTOX_ACCESS_TOKEN from .env

    # ── Example 1: Nifty 50 daily OHLCV ─────────────────────────────
    print("Fetching Nifty 50 daily data …")
    df = fetcher.fetch(
        instrument_key=NSE.NIFTY_50,
        interval=Interval.D1,
        from_date="2024-01-01",
        to_date="2024-12-31",
    )
    print(df.tail())
    print(f"Total candles: {len(df)}\n")

    # ── Example 2: BankNifty 30-minute candles ───────────────────────
    print("Fetching BankNifty 30-min data …")
    df2 = fetcher.fetch(
        instrument_key=NSE.BANKNIFTY,
        interval=Interval.I30M,
        from_date="2025-01-01",
        to_date="2025-03-31",
    )
    print(df2.head())
    print(f"Total candles: {len(df2)}\n")

    # ── Example 3: Save as parquet ───────────────────────────────────
    print("Saving Reliance weekly candles to parquet …")
    path = fetcher.fetch_and_save(
        instrument_key=NSE.RELIANCE,
        interval=Interval.W1,
        from_date="2023-01-01",
        to_date="2024-12-31",
        out_dir=Path("./data"),
        fmt="parquet",
    )
    print(f"Saved: {path}")


if __name__ == "__main__":
    main()
