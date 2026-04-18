"""
plotting.py — Plotly-based interactive charts.

Generates standalone HTML files with candlestick + volume + optional
technical-indicator overlays. All indicators are computed inline with
pandas/numpy — no TA-Lib dependency.

Available indicators
--------------------
- ``sma<N>``     — simple moving average over N periods   (e.g. sma20, sma50, sma200)
- ``ema<N>``     — exponential moving average over N periods
- ``bb<N>``      — Bollinger Bands (SMA ± 2σ over N periods; default N=20)
- ``rsi<N>``     — Relative Strength Index (default N=14). Rendered as a subplot.
- ``macd``       — MACD (12, 26, 9) in a subplot.
- ``vwap_overlay`` — overlay the per-day VWAP column if present.

Usage::

    from upstox_historical.plotting import plot_candles

    plot_candles(
        "data/NSE_EQ_INE002A01018_day_2020-01-01_2025-10-31.parquet",
        indicators=["sma20", "sma50", "bb20", "rsi14"],
        out="reliance.html",
    )
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Iterable, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ── Indicator calculations ───────────────────────────────────────────

def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window=window, min_periods=window).mean()


def ema(series: pd.Series, window: int) -> pd.Series:
    return series.ewm(span=window, adjust=False).mean()


def bollinger(series: pd.Series, window: int = 20, k: float = 2.0) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Return (upper, middle, lower) Bollinger bands."""
    mid = sma(series, window)
    std = series.rolling(window=window, min_periods=window).std()
    upper = mid + k * std
    lower = mid - k * std
    return upper, mid, lower


def rsi(series: pd.Series, window: int = 14) -> pd.Series:
    """Wilder-style RSI."""
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    # Wilder's smoothing ≈ EMA with alpha = 1/window
    avg_up = up.ewm(alpha=1 / window, adjust=False).mean()
    avg_down = down.ewm(alpha=1 / window, adjust=False).mean()
    rs = avg_up / avg_down.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    return out


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Return (macd_line, signal_line, histogram)."""
    fast_ema = ema(series, fast)
    slow_ema = ema(series, slow)
    macd_line = fast_ema - slow_ema
    signal_line = ema(macd_line, signal)
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


# ── Indicator-spec parsing ───────────────────────────────────────────

_RE_PERIODIC = re.compile(r"^(sma|ema|bb|rsi)(\d+)$", re.IGNORECASE)


def _parse_indicator(spec: str) -> tuple[str, Optional[int]]:
    """
    Parse an indicator spec string.

        'sma20'         → ('sma', 20)
        'rsi14'         → ('rsi', 14)
        'bb20'          → ('bb', 20)
        'macd'          → ('macd', None)
        'vwap_overlay'  → ('vwap_overlay', None)
    """
    s = spec.strip().lower()
    if s in {"macd", "vwap_overlay"}:
        return s, None
    m = _RE_PERIODIC.match(s)
    if m:
        return m.group(1), int(m.group(2))
    raise ValueError(f"Unknown indicator: {spec!r}")


# ── Loading ──────────────────────────────────────────────────────────

def load_candles(path: str | Path) -> pd.DataFrame:
    """Load a saved CSV or Parquet and normalise the timestamp column."""
    path = Path(path)
    if path.suffix == ".parquet":
        df = pd.read_parquet(path)
    elif path.suffix in (".csv", ".txt"):
        df = pd.read_csv(path)
    else:
        raise ValueError(f"Unsupported extension: {path.suffix} (expected .csv or .parquet)")

    # Normalise timestamp column name
    if "Date" in df.columns and "timestamp" not in df.columns:
        df = df.rename(columns={"Date": "timestamp"})

    if "timestamp" not in df.columns:
        raise ValueError(f"No 'timestamp' or 'Date' column in {path}")

    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)
    return df


# ── Main plot function ───────────────────────────────────────────────

def plot_candles(
    source: str | Path | pd.DataFrame,
    *,
    indicators: Iterable[str] = (),
    out: str | Path | None = None,
    title: str | None = None,
    open_browser: bool = False,
) -> Path:
    """
    Render a candlestick + volume chart with optional overlays and subplots.

    Parameters
    ----------
    source : path or DataFrame
        Either a path to a saved CSV/parquet, or an already-loaded DataFrame.
    indicators : iterable of str
        E.g. ``["sma20", "sma50", "bb20", "rsi14", "macd", "vwap_overlay"]``.
    out : path, optional
        Output HTML file. Defaults to ``<source>.html`` next to the source.
    title : str, optional
        Chart title. Derived from filename if omitted.
    open_browser : bool
        Open the HTML in the default browser after writing.

    Returns
    -------
    Path
        Path to the written HTML file.
    """
    # Lazy import — plotly is optional at import time
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
    except ImportError as exc:
        raise ImportError(
            "plotly is required for plotting. Install with: uv add plotly"
        ) from exc

    # Load if source is a path
    if isinstance(source, (str, Path)):
        df = load_candles(source)
        source_path = Path(source)
    else:
        df = source.copy()
        if "Date" in df.columns and "timestamp" not in df.columns:
            df = df.rename(columns={"Date": "timestamp"})
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        source_path = Path("candles")

    if df.empty:
        raise ValueError("Cannot plot an empty DataFrame")

    # Parse indicators and figure out how many subplot rows we need
    specs = [_parse_indicator(s) for s in indicators]
    has_rsi = any(k == "rsi" for k, _ in specs)
    has_macd = any(k == "macd" for k, _ in specs)

    # Row layout:
    #   1: price (candlestick + overlays)
    #   2: volume
    #   3: RSI (if requested)
    #   4: MACD (if requested)
    rows = 2 + (1 if has_rsi else 0) + (1 if has_macd else 0)
    row_heights = [0.55, 0.15]
    if has_rsi:
        row_heights.append(0.15)
    if has_macd:
        row_heights.append(0.15)
    # Normalise
    total = sum(row_heights)
    row_heights = [h / total for h in row_heights]

    fig = make_subplots(
        rows=rows, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.02,
        row_heights=row_heights,
    )

    # ── Row 1: candlesticks ──────────────────────────────────────────
    fig.add_trace(
        go.Candlestick(
            x=df["timestamp"],
            open=df["open"], high=df["high"],
            low=df["low"], close=df["close"],
            name="Price",
            increasing_line_color="#1d9e75",
            decreasing_line_color="#e24b4a",
        ),
        row=1, col=1,
    )

    # Overlays on row 1 (SMAs, EMAs, Bollinger, VWAP)
    close = df["close"].astype(float)
    overlay_palette = ["#378add", "#d85a30", "#7f77dd", "#ba7517", "#888780"]
    palette_idx = 0

    def _next_color() -> str:
        nonlocal palette_idx
        c = overlay_palette[palette_idx % len(overlay_palette)]
        palette_idx += 1
        return c

    for kind, period in specs:
        if kind == "sma":
            fig.add_trace(
                go.Scatter(
                    x=df["timestamp"], y=sma(close, period),
                    mode="lines", name=f"SMA{period}",
                    line=dict(width=1.2, color=_next_color()),
                ), row=1, col=1,
            )
        elif kind == "ema":
            fig.add_trace(
                go.Scatter(
                    x=df["timestamp"], y=ema(close, period),
                    mode="lines", name=f"EMA{period}",
                    line=dict(width=1.2, color=_next_color(), dash="dot"),
                ), row=1, col=1,
            )
        elif kind == "bb":
            up, mid, lo = bollinger(close, window=period)
            color = _next_color()
            fig.add_trace(
                go.Scatter(x=df["timestamp"], y=up, mode="lines",
                           name=f"BB{period} upper",
                           line=dict(width=0.8, color=color)),
                row=1, col=1,
            )
            fig.add_trace(
                go.Scatter(x=df["timestamp"], y=lo, mode="lines",
                           name=f"BB{period} lower",
                           line=dict(width=0.8, color=color),
                           fill="tonexty",
                           fillcolor="rgba(55,138,221,0.08)"),
                row=1, col=1,
            )
        elif kind == "vwap_overlay":
            if "vwap" in df.columns:
                fig.add_trace(
                    go.Scatter(
                        x=df["timestamp"], y=df["vwap"],
                        mode="lines", name="VWAP",
                        line=dict(width=1.0, color="#bf9017", dash="dash"),
                    ), row=1, col=1,
                )
            else:
                logger.warning("vwap_overlay requested but no 'vwap' column present")

    # ── Row 2: volume ────────────────────────────────────────────────
    if "volume" in df.columns:
        colors = np.where(df["close"] >= df["open"], "#1d9e75", "#e24b4a")
        fig.add_trace(
            go.Bar(
                x=df["timestamp"], y=df["volume"], name="Volume",
                marker_color=colors, opacity=0.7,
            ),
            row=2, col=1,
        )

    # ── Row 3: RSI ───────────────────────────────────────────────────
    cur_row = 3
    if has_rsi:
        period = next(p for k, p in specs if k == "rsi")
        rsi_vals = rsi(close, period or 14)
        fig.add_trace(
            go.Scatter(
                x=df["timestamp"], y=rsi_vals,
                mode="lines", name=f"RSI{period}",
                line=dict(width=1.2, color="#7f77dd"),
            ),
            row=cur_row, col=1,
        )
        # Overbought / oversold bands
        fig.add_hline(y=70, line=dict(color="rgba(226,75,74,0.4)", dash="dash"),
                      row=cur_row, col=1)
        fig.add_hline(y=30, line=dict(color="rgba(29,158,117,0.4)", dash="dash"),
                      row=cur_row, col=1)
        fig.update_yaxes(title_text=f"RSI({period})", range=[0, 100], row=cur_row, col=1)
        cur_row += 1

    # ── Row 4: MACD ──────────────────────────────────────────────────
    if has_macd:
        macd_line, signal_line, hist = macd(close)
        hist_colors = np.where(hist >= 0, "#1d9e75", "#e24b4a")
        fig.add_trace(
            go.Bar(x=df["timestamp"], y=hist, name="MACD hist",
                   marker_color=hist_colors, opacity=0.6),
            row=cur_row, col=1,
        )
        fig.add_trace(
            go.Scatter(x=df["timestamp"], y=macd_line, mode="lines",
                       name="MACD", line=dict(width=1.2, color="#378add")),
            row=cur_row, col=1,
        )
        fig.add_trace(
            go.Scatter(x=df["timestamp"], y=signal_line, mode="lines",
                       name="Signal", line=dict(width=1.2, color="#ba7517")),
            row=cur_row, col=1,
        )
        fig.update_yaxes(title_text="MACD", row=cur_row, col=1)

    # ── Layout ────────────────────────────────────────────────────────
    title = title or source_path.stem
    fig.update_layout(
        title=title,
        xaxis_rangeslider_visible=False,
        template="plotly_white",
        height=220 * rows + 120,
        margin=dict(l=60, r=40, t=70, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="right", x=1),
        hovermode="x unified",
    )
    fig.update_yaxes(title_text="Price", row=1, col=1)
    if "volume" in df.columns:
        fig.update_yaxes(title_text="Volume", row=2, col=1)

    # Write HTML
    if out is None:
        out = source_path.with_suffix(".html")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.write_html(str(out), include_plotlyjs="cdn")
    logger.info("Wrote %s (%.1f KB)", out, out.stat().st_size / 1024)

    if open_browser:
        import webbrowser
        webbrowser.open(out.resolve().as_uri())

    return out
