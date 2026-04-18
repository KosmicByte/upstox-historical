"""
updater.py — Incremental update of existing saved candle files.

Given a CSV or Parquet produced by ``HistoricalFetcher.fetch_and_save()``,
reads the last timestamp, fetches only the missing range to ``today``
(or any user-specified end date), and appends/dedupes in place.

Use cases:

- Daily cron: ``upstox-fetch update ./data/NIFTY_day_*.parquet`` after
  market close appends yesterday's + today's candles in seconds.
- SPDE calibration pipeline: keep your training dataset up-to-date without
  re-downloading the whole history.

The file's existing filename encodes (instrument_key, interval, from, to),
so the updater can derive fetch parameters automatically.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

from upstox_historical.async_fetcher import AsyncHistoricalFetcher

logger = logging.getLogger(__name__)


# ── Filename parsing ─────────────────────────────────────────────────

# Known Upstox instrument-key prefixes. Order matters: longer prefixes
# must be listed before shorter ones so regex alternation picks the
# most specific match first. Every prefix is presented in its
# filename form (pipes replaced with underscores).
_KNOWN_PREFIXES = [
    "NSE_INDEX",
    "BSE_INDEX",
    "NSE_EQ",
    "NSE_BE",
    "NSE_SM",
    "BSE_EQ",
    "MCX_FO",
    "NFO_FO",
    "NFO_OPT",
    "BFO_FO",
    "BFO_OPT",
]

_FILENAME_RE = re.compile(
    r"^(?P<prefix>" + "|".join(_KNOWN_PREFIXES) + r")_"
    r"(?P<suffix>.+?)_"
    r"(?P<interval>1minute|2minute|3minute|5minute|10minute|15minute|20minute|25minute|30minute|day|week|month)_"
    r"(?P<from>\d{4}-\d{2}-\d{2})_"
    r"(?P<to>\d{4}-\d{2}-\d{2})"
    r"\.(?P<ext>csv|parquet)$"
)


@dataclass
class UpdatePlan:
    """What an update will do, without actually doing it."""

    path: Path
    instrument_key: str
    interval: str
    existing_from: date
    existing_to: date
    new_from: date
    new_to: date
    skip: bool  # already up-to-date?


def parse_filename(path: str | Path) -> dict[str, str]:
    """
    Parse a canonical upstox-historical filename back to its components.

    Example::

        parse_filename("NSE_EQ_INE002A01018_day_2020-01-01_2025-10-31.parquet")
        # → {
        #     "instrument_key": "NSE_EQ|INE002A01018",   # note: _ → |
        #     "interval": "day",
        #     "from_date": "2020-01-01",
        #     "to_date": "2025-10-31",
        #     "ext": "parquet",
        #   }
    """
    path = Path(path)
    m = _FILENAME_RE.match(path.name)
    if not m:
        raise ValueError(
            f"Can't parse filename {path.name!r}. Expected "
            f"<prefix>_<suffix>_<interval>_<from>_<to>.(csv|parquet) where "
            f"<prefix> is one of: {', '.join(_KNOWN_PREFIXES)}"
        )

    prefix = m.group("prefix")
    # Suffix may contain underscores that originally were spaces
    # (e.g. "Nifty 50" → "Nifty_50" in filename → "Nifty 50" here).
    # For ISIN-style suffixes ("INE002A01018") there are no underscores so no-op.
    suffix = m.group("suffix").replace("_", " ")
    instrument_key = f"{prefix}|{suffix}"

    return {
        "instrument_key": instrument_key,
        "interval": m.group("interval"),
        "from_date": m.group("from"),
        "to_date": m.group("to"),
        "ext": m.group("ext"),
    }


# ── Loading/saving helpers ───────────────────────────────────────────

def _load(path: Path) -> pd.DataFrame:
    if path.suffix == ".parquet":
        return pd.read_parquet(path)
    return pd.read_csv(path)


def _save(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".parquet":
        df.to_parquet(path, index=False)
    else:
        df.to_csv(path, index=False)


def _timestamp_col(df: pd.DataFrame) -> str:
    if "timestamp" in df.columns:
        return "timestamp"
    if "Date" in df.columns:
        return "Date"
    raise ValueError("DataFrame has neither 'timestamp' nor 'Date' column")


# ── Plan + execute ───────────────────────────────────────────────────

def plan_update(
    path: str | Path,
    until: Optional[str] = None,
) -> UpdatePlan:
    """
    Inspect an existing saved file and describe what an update would fetch.
    Doesn't actually fetch anything.

    Parameters
    ----------
    path : path
        Existing .csv or .parquet.
    until : str, optional
        End date YYYY-MM-DD. Defaults to today.
    """
    path = Path(path)
    meta = parse_filename(path)

    df = _load(path)
    if df.empty:
        raise ValueError(f"Existing file is empty: {path}")

    ts_col = _timestamp_col(df)
    ts = pd.to_datetime(df[ts_col])
    existing_from = ts.min().date()
    existing_to = ts.max().date()

    end_date = date.fromisoformat(until) if until else date.today()
    new_from = existing_to + timedelta(days=1)
    skip = new_from > end_date

    return UpdatePlan(
        path=path,
        instrument_key=meta["instrument_key"],
        interval=meta["interval"],
        existing_from=existing_from,
        existing_to=existing_to,
        new_from=new_from,
        new_to=end_date,
        skip=skip,
    )


async def update(
    path: str | Path,
    *,
    until: Optional[str] = None,
    nse_enrich: Optional[bool] = None,   # None = auto-detect from existing columns
    symbol: Optional[str] = None,
    series: Optional[str] = None,
    rename_output: bool = True,
) -> UpdatePlan:
    """
    Incrementally update an existing file with candles since its last timestamp.

    Parameters
    ----------
    path : path
        File to update (in-place).
    until : str, optional
        End date YYYY-MM-DD. Defaults to today.
    nse_enrich : bool, optional
        Whether to enrich new candles. If None, auto-detects based on the
        presence of NSE columns (``Prev Close``, ``Trades``) in the existing file.
    symbol, series : str, optional
        For NSE enrichment of new candles.
    rename_output : bool
        If True (default), renames the file on disk to reflect the new date range.

    Returns
    -------
    UpdatePlan
        Describes what was done. ``.skip=True`` if the file was already current.
    """
    plan = plan_update(path, until=until)

    if plan.skip:
        logger.info(
            "Already up-to-date: %s (last=%s, target=%s)",
            plan.path, plan.existing_to, plan.new_to,
        )
        return plan

    df_old = _load(plan.path)
    ts_col = _timestamp_col(df_old)

    # Auto-detect enrichment from existing columns if not overridden
    if nse_enrich is None:
        nse_enrich = "Prev Close" in df_old.columns or "Trades" in df_old.columns
        if nse_enrich:
            logger.info("Auto-detected NSE enrichment from existing columns")

    # Auto-detect symbol from existing Symbol column if not specified
    if nse_enrich and symbol is None and "Symbol" in df_old.columns:
        unique_syms = df_old["Symbol"].dropna().unique()
        if len(unique_syms) == 1:
            symbol = str(unique_syms[0])
            logger.info("Auto-detected symbol: %s", symbol)

    fetcher = AsyncHistoricalFetcher()
    logger.info(
        "Update: fetching %s %s from %s to %s",
        plan.instrument_key, plan.interval, plan.new_from, plan.new_to,
    )
    df_new = await fetcher.fetch(
        instrument_key=plan.instrument_key,
        interval=plan.interval,
        from_date=plan.new_from.isoformat(),
        to_date=plan.new_to.isoformat(),
        nse_enrich=nse_enrich,
        symbol=symbol,
        series=series,
    )

    if df_new.empty:
        logger.warning("Update fetched 0 rows — market may have been closed for the window.")
        return plan

    # Align column names: if old file has 'Date' (post-enrichment) and new has 'timestamp',
    # rename new to match old, or vice versa.
    new_ts = _timestamp_col(df_new)
    if ts_col != new_ts:
        df_new = df_new.rename(columns={new_ts: ts_col})

    # Ensure both frames use datetime dtypes on the timestamp column
    df_old[ts_col] = pd.to_datetime(df_old[ts_col])
    df_new[ts_col] = pd.to_datetime(df_new[ts_col])

    # Align columns — prefer intersection, fill missing with NaN
    all_cols = list(df_old.columns)
    for c in df_new.columns:
        if c not in all_cols:
            all_cols.append(c)
    df_old = df_old.reindex(columns=all_cols)
    df_new = df_new.reindex(columns=all_cols)

    combined = (
        pd.concat([df_old, df_new], ignore_index=True)
        .drop_duplicates(subset=[ts_col], keep="last")
        .sort_values(ts_col)
        .reset_index(drop=True)
    )

    new_added = len(combined) - len(df_old)
    logger.info(
        "Update complete: +%d new rows (total now %d, was %d)",
        new_added, len(combined), len(df_old),
    )

    # Optionally rename file to reflect new date range
    target_path = plan.path
    if rename_output:
        meta = parse_filename(plan.path)
        key_safe = meta["instrument_key"].replace("|", "_").replace(" ", "_")
        new_stem = f"{key_safe}_{meta['interval']}_{meta['from_date']}_{plan.new_to.isoformat()}"
        target_path = plan.path.with_name(f"{new_stem}.{meta['ext']}")
        if target_path != plan.path and target_path.exists():
            # If someone already did a parallel update, just overwrite
            target_path.unlink()

    _save(combined, target_path)

    # Remove old file if rename happened and it's a different path
    if rename_output and target_path != plan.path:
        try:
            plan.path.unlink()
        except OSError:
            pass
        logger.info("Renamed: %s → %s", plan.path.name, target_path.name)
        plan.path = target_path

    return plan
