"""
cache.py — Persistent on-disk caches.

Two caches live here:

1. **Bhav Copy cache** — one parquet per trading date. Never expires
   (NSE Bhav Copies are immutable historical records). Dramatically
   reduces NSE round-trips when the same date is enriched repeatedly.

2. **Chunk checkpoint cache** — one parquet per chunk of a running fetch,
   keyed by (instrument_key, interval, from_date, to_date, chunk_from, chunk_to).
   Enables resume-on-failure: if a multi-chunk fetch is interrupted, the
   next run re-uses already-completed chunks instead of re-downloading.

Both caches live under ``$XDG_CACHE_HOME/upstox-historical/`` (falls back
to ``~/.cache/upstox-historical/`` on Linux/Mac, ``%LOCALAPPDATA%/upstox-historical/``
on Windows).
"""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

# ── Root cache directory ──────────────────────────────────────────────

def _default_cache_root() -> Path:
    """Return platform-appropriate cache root directory."""
    env = os.environ.get("UPSTOX_CACHE_DIR")
    if env:
        return Path(env).expanduser()
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg) / "upstox-historical"
    if os.name == "nt":  # Windows
        local = os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
        return Path(local) / "upstox-historical" / "cache"
    return Path.home() / ".cache" / "upstox-historical"


_CACHE_ROOT = _default_cache_root()
_BHAV_DIR = _CACHE_ROOT / "bhav"
_CKPT_DIR = _CACHE_ROOT / "checkpoints"


def cache_root() -> Path:
    """Return current cache root (computed once per import)."""
    return _CACHE_ROOT


# ── Bhav Copy cache ──────────────────────────────────────────────────

def bhav_cache_path(trade_date: date) -> Path:
    """Path where the Bhav Copy parquet for a given trade date lives."""
    _BHAV_DIR.mkdir(parents=True, exist_ok=True)
    return _BHAV_DIR / f"{trade_date.isoformat()}.parquet"


def bhav_cache_miss_marker(trade_date: date) -> Path:
    """
    Marker file for 'we tried and the file wasn't there' (holiday/weekend).
    Prevents hammering NSE repeatedly for non-trading days.
    """
    _BHAV_DIR.mkdir(parents=True, exist_ok=True)
    return _BHAV_DIR / f"{trade_date.isoformat()}.miss"


def bhav_load(trade_date: date) -> Optional[pd.DataFrame]:
    """
    Return the cached Bhav Copy DataFrame for this date, or None if not cached.
    Also returns None if a 'miss marker' exists (previously confirmed non-trading day).
    """
    miss = bhav_cache_miss_marker(trade_date)
    if miss.exists():
        logger.debug("Bhav cache: miss marker present for %s — skip fetch", trade_date)
        return None
    path = bhav_cache_path(trade_date)
    if path.exists():
        try:
            df = pd.read_parquet(path)
            logger.debug("Bhav cache HIT: %s (%d rows)", trade_date, len(df))
            return df
        except Exception as exc:
            logger.warning("Bhav cache corrupted for %s: %s — re-fetching", trade_date, exc)
            try:
                path.unlink()
            except OSError:
                pass
            return None
    return None


def bhav_store(trade_date: date, df: pd.DataFrame) -> None:
    """Write a freshly-fetched Bhav Copy DataFrame to the cache."""
    path = bhav_cache_path(trade_date)
    try:
        df.to_parquet(path, index=False)
        logger.debug("Bhav cache STORE: %s (%d rows)", trade_date, len(df))
    except Exception as exc:
        logger.warning("Bhav cache write failed for %s: %s", trade_date, exc)


def bhav_mark_miss(trade_date: date) -> None:
    """Record that this date has no Bhav Copy (holiday/weekend/future)."""
    try:
        bhav_cache_miss_marker(trade_date).touch()
    except OSError as exc:
        logger.debug("Could not write miss marker for %s: %s", trade_date, exc)


def bhav_cache_stats() -> dict[str, int]:
    """Return counts for diagnostics."""
    if not _BHAV_DIR.exists():
        return {"hits_available": 0, "miss_markers": 0, "size_mb": 0}
    parquets = list(_BHAV_DIR.glob("*.parquet"))
    misses = list(_BHAV_DIR.glob("*.miss"))
    size_bytes = sum(p.stat().st_size for p in parquets)
    return {
        "hits_available": len(parquets),
        "miss_markers": len(misses),
        "size_mb": round(size_bytes / 1e6, 2),
    }


def bhav_cache_clear() -> int:
    """Wipe the entire Bhav Copy cache. Returns number of files removed."""
    if not _BHAV_DIR.exists():
        return 0
    count = 0
    for p in _BHAV_DIR.iterdir():
        try:
            p.unlink()
            count += 1
        except OSError:
            pass
    return count


# ── Chunk checkpoint cache ───────────────────────────────────────────

def _checkpoint_key(
    instrument_key: str,
    interval: str,
    from_date: str,
    to_date: str,
) -> str:
    """Stable 12-char hash of a fetch job's parameters."""
    raw = f"{instrument_key}|{interval}|{from_date}|{to_date}"
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def checkpoint_dir(
    instrument_key: str,
    interval: str,
    from_date: str,
    to_date: str,
) -> Path:
    """Return directory holding one parquet per chunk for this fetch job."""
    key = _checkpoint_key(instrument_key, interval, from_date, to_date)
    d = _CKPT_DIR / key
    d.mkdir(parents=True, exist_ok=True)
    # Drop a breadcrumb so humans can identify what's in a hash folder
    info = d / ".info.txt"
    if not info.exists():
        try:
            info.write_text(
                f"instrument_key = {instrument_key}\n"
                f"interval       = {interval}\n"
                f"from_date      = {from_date}\n"
                f"to_date        = {to_date}\n"
            )
        except OSError:
            pass
    return d


def checkpoint_path(ckpt_dir: Path, chunk_idx: int) -> Path:
    """Path for a specific chunk's parquet (1-indexed, zero-padded for sort order)."""
    return ckpt_dir / f"chunk_{chunk_idx:04d}.parquet"


def checkpoint_load(ckpt_dir: Path, chunk_idx: int) -> Optional[pd.DataFrame]:
    """Load a chunk's cached DataFrame if it exists, else None."""
    path = checkpoint_path(ckpt_dir, chunk_idx)
    if not path.exists():
        return None
    try:
        return pd.read_parquet(path)
    except Exception as exc:
        logger.warning("Checkpoint corrupted (%s): %s — will refetch", path, exc)
        try:
            path.unlink()
        except OSError:
            pass
        return None


def checkpoint_store(ckpt_dir: Path, chunk_idx: int, df: pd.DataFrame) -> None:
    """Persist a chunk's DataFrame to disk."""
    path = checkpoint_path(ckpt_dir, chunk_idx)
    try:
        df.to_parquet(path, index=False)
    except Exception as exc:
        logger.warning("Checkpoint write failed (%s): %s", path, exc)


def checkpoint_clear(ckpt_dir: Path) -> None:
    """Remove a completed job's checkpoint directory."""
    try:
        shutil.rmtree(ckpt_dir, ignore_errors=True)
    except Exception as exc:
        logger.debug("Checkpoint cleanup failed: %s", exc)


def checkpoint_stats() -> dict[str, int]:
    """Return aggregate checkpoint cache stats."""
    if not _CKPT_DIR.exists():
        return {"active_jobs": 0, "total_chunks": 0, "size_mb": 0}
    jobs = [d for d in _CKPT_DIR.iterdir() if d.is_dir()]
    chunks = sum(len(list(d.glob("chunk_*.parquet"))) for d in jobs)
    size_bytes = sum(
        p.stat().st_size for d in jobs for p in d.glob("chunk_*.parquet")
    )
    return {
        "active_jobs": len(jobs),
        "total_chunks": chunks,
        "size_mb": round(size_bytes / 1e6, 2),
    }
