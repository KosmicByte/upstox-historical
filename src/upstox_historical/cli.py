"""
cli.py — Typer-based CLI for upstox-historical.

Commands
────────
  upstox-fetch fetch     Fetch historical OHLCV candles
  upstox-fetch intraday  Fetch today's intraday candles
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
    help="Fetch historical OHLCV data from the Upstox v2 API.",
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
    from datetime import date as _date
    fetch_as   = _FETCH_AS.get(interval.value, interval.value)
    chunk_size = _CHUNK_SIZE[fetch_as]
    n_chunks   = sum(1 for _ in _date_chunks(from_date, to_date, chunk_size))
    console.print(
        f"[dim]Interval:[/] [cyan]{interval.value}[/]  ",
        f"[dim]Chunks:[/] [yellow]{n_chunks}[/]  ",
        f"[dim]Est. time:[/] [yellow]~{max(1, round(n_chunks * 0.7))}s[/]",
    )
    if nse_enrich:
        console.print(f"[dim]NSE Enrichment:[/] [green]enabled[/]  [dim]Symbol:[/] [cyan]{symbol or 'auto'}[/]  [dim]Series:[/] [cyan]{series or 'auto'}[/]")

    from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, MofNCompleteColumn, TimeElapsedColumn

    # Suppress log output while progress bars are active — otherwise logger.info()
    # from fetcher/nse_enrichment causes the bar to reprint on every line.
    _loggers_to_hush = [
        logging.getLogger("upstox_historical.fetcher"),
        logging.getLogger("upstox_historical.nse_enrichment"),
        logging.getLogger("upstox_historical.client"),
        logging.getLogger("httpx"),
    ]
    _saved_levels = {lg: lg.level for lg in _loggers_to_hush}
    for lg in _loggers_to_hush:
        lg.setLevel(logging.WARNING)

    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[bold]{task.description}"),
            BarColumn(bar_width=40),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            console=console,
            transient=False,
        ) as progress:
            # Phase 1: Upstox API chunks
            task_api = progress.add_task("[cyan]Upstox API", total=n_chunks)

            def _on_chunk(current: int, total: int) -> None:
                progress.update(task_api, completed=current)

            # Phase 2 & 3: NSE Bhav + Merge (created dynamically when needed)
            task_bhav = None
            task_merge = None

            def _on_bhav(current: int, total: int) -> None:
                nonlocal task_bhav
                if task_bhav is None:
                    task_bhav = progress.add_task("[yellow]NSE Bhav Copy", total=total)
                progress.update(task_bhav, completed=current)

            def _on_merge() -> None:
                nonlocal task_merge
                task_merge = progress.add_task("[green]Merging data", total=1)

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
                on_chunk_progress=_on_chunk,
                on_bhav_progress=_on_bhav if nse_enrich else None,
                on_merge_start=_on_merge if nse_enrich else None,
            )
            if task_merge is not None:
                progress.update(task_merge, completed=1)
    finally:
        # Restore log levels
        for lg, lvl in _saved_levels.items():
            lg.setLevel(lvl)

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
        typer.Option("--nse-enrich", help="Enrich with NSE Bhav Copy (Prev Close, Trades, Deliverable, Turnover)."),
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

    # Save
    from upstox_historical.fetcher import HistoricalFetcher
    from datetime import date
    today = date.today().isoformat()
    path = fetcher._save(df, instrument_key, interval, today, today, out_dir, fmt)
    console.print(f"[bold green]✓[/] Saved → [cyan]{path}[/]")

    if preview:
        _render_table(instrument_key, df.head(10))


# ── login ─────────────────────────────────────────────────────────────

@app.command()
def login(
    redirect_uri: Annotated[
        str,
        typer.Option("--redirect-uri", "-r", help="OAuth redirect URI."),
    ] = "http://localhost:8000/callback",
) -> None:
    """Run the interactive OAuth login flow to get an access token."""
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
