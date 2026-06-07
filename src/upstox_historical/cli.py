"""
cli.py — Typer-based CLI for upstox-historical.

Commands
────────
  upstox-fetch fetch     Fetch historical OHLCV candles
  upstox-fetch intraday  Fetch today's intraday candles
  upstox-fetch quote     Get live LTP / OHLC quote (Analytics Token)
  upstox-fetch stream    Live WebSocket feed with candle building
  upstox-fetch login     Run the OAuth login flow
  upstox-fetch keys      Print known instrument keys
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.table import Table

from upstox_historical.config import configure_logging, get_settings
from upstox_historical.fetcher import HistoricalFetcher
from upstox_historical.instruments import NSE, list_keys
from upstox_historical.models import Interval

app = typer.Typer(
    name="upstox-fetch",
    help="Fetch historical OHLCV data and stream live market data from Upstox.",
    add_completion=False,
)
console = Console()


def _version_callback(value: bool) -> None:
    if value:
        from upstox_historical import __version__
        typer.echo(f"upstox-historical {__version__}")
        raise typer.Exit()


@app.callback()
def global_options(
    version: Annotated[
        Optional[bool],
        typer.Option("--version", callback=_version_callback, is_eager=True),
    ] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Enable DEBUG logging.")] = False,
) -> None:
    level = "DEBUG" if verbose else get_settings().log_level
    configure_logging(level)


# ── fetch ─────────────────────────────────────────────────────────────

@app.command()
def fetch(
    instrument_key: Annotated[
        str,
        typer.Argument(help='Upstox instrument key, e.g. "NSE_INDEX|Nifty 50"'),
    ],
    interval: Annotated[
        Interval,
        typer.Option("--interval", "-i", help="Candle interval."),
    ] = Interval.D1,
    from_date: Annotated[
        str,
        typer.Option("--from", "-f", help="Start date YYYY-MM-DD."),
    ] = "2024-01-01",
    to_date: Annotated[
        str,
        typer.Option("--to", "-t", help="End date   YYYY-MM-DD."),
    ] = "2024-12-31",
    out_dir: Annotated[
        Path,
        typer.Option("--out-dir", "-o", help="Output directory."),
    ] = Path("./data"),
    fmt: Annotated[
        str,
        typer.Option("--format", help="Output format: csv | parquet."),
    ] = "csv",
    preview: Annotated[
        bool,
        typer.Option("--preview", help="Print the first 10 rows in the terminal."),
    ] = True,
    nse_enrich: Annotated[
        bool,
        typer.Option("--nse-enrich", help="Enrich with NSE Bhav Copy (Prev Close, Trades, Deliverable, Turnover)."),
    ] = False,
    symbol: Annotated[
        Optional[str],
        typer.Option("--symbol", "-s", help='NSE ticker symbol, e.g. "RELIANCE". Required when --nse-enrich is set.'),
    ] = None,
    series: Annotated[
        Optional[str],
        typer.Option("--series", help='NSE series, e.g. "EQ". Defaults to EQ for NSE_EQ instruments.'),
    ] = None,
) -> None:
    """Fetch historical OHLCV candles and save to disk."""

    fetcher = HistoricalFetcher()

    from upstox_historical.fetcher import _CHUNK_SIZE, _FETCH_AS, _date_chunks
    fetch_as   = _FETCH_AS.get(interval.value, interval.value)
    chunk_size = _CHUNK_SIZE[fetch_as]
    n_chunks   = sum(1 for _ in _date_chunks(from_date, to_date, chunk_size))
    console.print(
        f"[dim]Interval:[/] [cyan]{interval.value}[/]  ",
        f"[dim]Chunks:[/] [yellow]{n_chunks}[/]  ",
        f"[dim]Est. time:[/] [yellow]~{max(1, round(n_chunks * 0.7))}s[/]",
    )
    if nse_enrich:
        console.print(
            f"[dim]NSE Enrichment:[/] [green]enabled[/]  "
            f"[dim]Symbol:[/] [cyan]{symbol or 'auto'}[/]  "
            f"[dim]Series:[/] [cyan]{series or 'auto'}[/]"
        )

    with console.status(f"[bold green]Fetching {n_chunks} chunk(s) …  (press Ctrl+C to cancel)"):
        saved_path = fetcher.fetch_and_save(
            instrument_key=instrument_key,
            interval=interval,
            from_date=from_date,
            to_date=to_date,
            out_dir=out_dir,
            fmt=fmt,
            nse_enrich=nse_enrich,
            symbol=symbol,
            series=series,
        )

    console.print(f"[bold green]✓[/] Saved → [cyan]{saved_path}[/]")

    if preview:
        df = fetcher.fetch(
            instrument_key, interval, from_date, to_date,
            nse_enrich=nse_enrich, symbol=symbol, series=series,
        )
        _render_table(instrument_key, df.head(10))


# ── intraday ──────────────────────────────────────────────────────────

@app.command()
def intraday(
    instrument_key: Annotated[
        str,
        typer.Argument(help='Upstox instrument key, e.g. "NSE_INDEX|Nifty 50"'),
    ],
    interval: Annotated[
        Interval,
        typer.Option("--interval", "-i"),
    ] = Interval.I1M,
    out_dir: Annotated[
        Path,
        typer.Option("--out-dir", "-o"),
    ] = Path("./data"),
    fmt: Annotated[str, typer.Option("--format")] = "csv",
    preview: Annotated[bool, typer.Option("--preview")] = True,
    nse_enrich: Annotated[
        bool,
        typer.Option("--nse-enrich", help="Enrich with NSE Bhav Copy."),
    ] = False,
    symbol: Annotated[
        Optional[str],
        typer.Option("--symbol", "-s", help='NSE ticker symbol, e.g. "RELIANCE".'),
    ] = None,
    series: Annotated[
        Optional[str],
        typer.Option("--series", help='NSE series, e.g. "EQ".'),
    ] = None,
) -> None:
    """Fetch today's intraday candles."""
    fetcher = HistoricalFetcher()

    with console.status("[bold green]Fetching intraday data …"):
        df = fetcher.fetch_intraday(
            instrument_key, interval,
            nse_enrich=nse_enrich, symbol=symbol, series=series,
        )

    if df.empty:
        console.print("[yellow]No intraday data returned (market closed?)[/]")
        raise typer.Exit()

    from datetime import date
    today = date.today().isoformat()
    path = fetcher._save(df, instrument_key, interval, today, today, out_dir, fmt)
    console.print(f"[bold green]✓[/] Saved → [cyan]{path}[/]")

    if preview:
        _render_table(instrument_key, df.head(10))


# ── quote ─────────────────────────────────────────────────────────────

@app.command()
def quote(
    instrument_keys: Annotated[
        list[str],
        typer.Argument(help='One or more instrument keys, e.g. "NSE_INDEX|Nifty 50"'),
    ],
    mode: Annotated[
        str,
        typer.Option("--mode", "-m", help="Quote type: ltp | ohlc | full"),
    ] = "ltp",
) -> None:
    """
    Get a live market quote using the Analytics Token (no daily reauth needed).

    Examples
    --------
      upstox-fetch quote "NSE_INDEX|Nifty 50"
      upstox-fetch quote "NSE_INDEX|Nifty 50" "NSE_INDEX|Nifty Bank" --mode ohlc
    """
    from upstox_historical.market_info import (
        get_ltp_many,
        get_ohlc,
        is_market_open,
    )

    settings = get_settings()
    if not settings.analytics_token and not settings.access_token:
        console.print("[red]✗[/] No token found. Set UPSTOX_ANALYTICS_TOKEN in .env")
        raise typer.Exit(1)

    # Market status
    try:
        open_status = is_market_open()
        status_str = "[green]OPEN[/]" if open_status else "[red]CLOSED[/]"
        console.print(f"Market: {status_str}")
    except Exception:
        console.print("[dim]Market status unavailable[/]")

    if mode == "ltp":
        with console.status("[bold green]Fetching LTP …"):
            prices = get_ltp_many(instrument_keys)

        table = Table(title="Live LTP", show_lines=True)
        table.add_column("Instrument", style="cyan")
        table.add_column("LTP (₹)", style="bold green", justify="right")
        for key, ltp in prices.items():
            table.add_row(key, f"{ltp:,.2f}")
        console.print(table)

    elif mode == "ohlc":
        table = Table(title="OHLC Quote", show_lines=True)
        table.add_column("Instrument", style="cyan")
        table.add_column("Open", justify="right")
        table.add_column("High", style="green", justify="right")
        table.add_column("Low", style="red", justify="right")
        table.add_column("Close", style="bold", justify="right")
        table.add_column("Volume", justify="right")

        with console.status("[bold green]Fetching OHLC …"):
            for key in instrument_keys:
                try:
                    q = get_ohlc(key)
                    table.add_row(
                        key,
                        f"{q.open:,.2f}",
                        f"{q.high:,.2f}",
                        f"{q.low:,.2f}",
                        f"{q.close:,.2f}",
                        f"{q.volume:,}",
                    )
                except Exception as exc:
                    table.add_row(key, "—", "—", "—", f"[red]Error: {exc}[/]", "—")
        console.print(table)

    else:
        console.print(f"[red]Unknown mode: {mode!r}. Use: ltp | ohlc | full[/]")
        raise typer.Exit(1)


# ── stream ────────────────────────────────────────────────────────────

@app.command()
def stream(
    instrument_keys: Annotated[
        list[str],
        typer.Argument(help='One or more instrument keys to subscribe.'),
    ],
    mode: Annotated[
        str,
        typer.Option("--mode", "-m", help="Feed mode: ltpc | full_d5 | full_d30 | option_greeks"),
    ] = "ltpc",
    candle_interval: Annotated[
        int,
        typer.Option("--candle", "-c", help="Build candles of this many minutes (0 = disable)."),
    ] = 0,
    show_candles: Annotated[
        bool,
        typer.Option("--show-candles", help="Print completed candles to console."),
    ] = True,
) -> None:
    """
    Start a live WebSocket feed and stream ticks to the console.

    Uses the Analytics Token — no daily reauth needed.

    Examples
    --------
      upstox-fetch stream "NSE_INDEX|Nifty 50"
      upstox-fetch stream "NSE_INDEX|Nifty 50" "NSE_INDEX|Nifty Bank" --mode full_d5
      upstox-fetch stream "NSE_INDEX|Nifty 50" --candle 5
    """
    import asyncio
    from datetime import datetime, timezone, timedelta

    from upstox_historical.stream import LiveStream, Tick
    from upstox_historical.market_info import is_market_open

    settings = get_settings()
    if not settings.analytics_token and not settings.access_token:
        console.print("[red]✗[/] No token found. Set UPSTOX_ANALYTICS_TOKEN in .env")
        raise typer.Exit(1)

    # Market gate check
    try:
        if not is_market_open():
            console.print(
                "[yellow]⚠[/]  Market is currently [red]CLOSED[/]. "
                "Stream will connect but may receive no ticks until market opens."
            )
        else:
            console.print("[green]✓[/] Market is [bold green]OPEN[/]")
    except Exception:
        console.print("[dim]Could not check market status — proceeding anyway.[/]")

    # Setup candle builder if requested
    candle_builder = None
    if candle_interval > 0:
        from upstox_historical.candle_builder import CandleBuilder

        def on_candle_closed(candle) -> None:
            if show_candles:
                ist = timezone(timedelta(hours=5, minutes=30))
                t = candle.open_time.strftime("%H:%M")
                console.print(
                    f"[dim]{t}[/] [cyan]{candle.instrument_key}[/] "
                    f"[dim]{candle_interval}m[/] "
                    f"O=[white]{candle.open:.2f}[/] "
                    f"H=[green]{candle.high:.2f}[/] "
                    f"L=[red]{candle.low:.2f}[/] "
                    f"C=[bold]{candle.close:.2f}[/] "
                    f"V=[dim]{candle.volume:,}[/]"
                )

        candle_builder = CandleBuilder(
            interval_minutes=candle_interval,
            on_candle=on_candle_closed,
        )
        console.print(f"[dim]Candle builder:[/] [cyan]{candle_interval}m intervals[/]")

    # Tick display
    def on_tick(tick: Tick) -> None:
        # Feed into candle builder if active
        if candle_builder:
            candle_builder.on_tick(tick)

        # Console output — show raw ticks only when no candle builder active
        if not candle_builder:
            spread_str = f"  spread=[yellow]{tick.spread:.2f}[/]" if tick.spread else ""
            console.print(
                f"[dim]{tick.instrument_key}[/]  "
                f"LTP=[bold green]{tick.ltp:,.2f}[/]  "
                f"close=[dim]{tick.close_price:,.2f}[/]"
                f"{spread_str}"
            )

    def on_status(status: str) -> None:
        icons = {"connected": "🟢", "disconnected": "🔴", "reconnecting": "🟡"}
        console.print(f"{icons.get(status, '•')} Stream [bold]{status}[/]")

    def on_error(exc: Exception) -> None:
        console.print(f"[red]✗ Stream error:[/] {exc}")

    console.print(
        f"\n[bold]Starting live stream[/] | "
        f"instruments=[cyan]{len(instrument_keys)}[/] | "
        f"mode=[cyan]{mode}[/]\n"
        f"[dim]Press Ctrl+C to stop.[/]\n"
    )

    live_stream = LiveStream(
        instrument_keys=instrument_keys,
        mode=mode,
        on_tick=on_tick,
        on_status=on_status,
        on_error=on_error,
    )

    try:
        asyncio.run(live_stream.connect())
    except KeyboardInterrupt:
        console.print("\n[yellow]Stream stopped by user.[/]")
        if candle_builder:
            flushed = candle_builder.flush()
            if flushed:
                console.print(f"[dim]Flushed {len(flushed)} open candle(s).[/]")


# ── login ─────────────────────────────────────────────────────────────

@app.command()
def login(
    redirect_uri: Annotated[
        str,
        typer.Option("--redirect-uri", "-r", help="OAuth redirect URI."),
    ] = "http://localhost:8000/callback",
) -> None:
    """Run the interactive OAuth login flow to get a daily access token."""
    from upstox_historical.auth import interactive_login
    interactive_login(redirect_uri)


# ── keys ──────────────────────────────────────────────────────────────

@app.command()
def keys() -> None:
    """Print all built-in NSE instrument keys."""
    list_keys()


# ── helpers ───────────────────────────────────────────────────────────

def _render_table(title: str, df) -> None:  # type: ignore[type-arg]
    table = Table(title=f"Preview — {title}", show_lines=True)
    for col in df.columns:
        table.add_column(str(col), style="cyan" if col == "timestamp" else "white")
    for _, row in df.iterrows():
        table.add_row(*[str(v) for v in row])
    console.print(table)


if __name__ == "__main__":
    app()
