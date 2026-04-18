"""
cli.py — Typer-based CLI for upstox-historical.

Commands
────────
  upstox-fetch fetch      Fetch historical OHLCV candles (single instrument)
  upstox-fetch batch      Fetch many instruments concurrently
  upstox-fetch update     Incrementally update an existing saved file
  upstox-fetch intraday   Fetch today's intraday candles
  upstox-fetch plot       Render an interactive Plotly chart of a saved file
  upstox-fetch validate   Run data-quality checks on a saved file
  upstox-fetch find       Interactive instrument search
  upstox-fetch cache      Show or clear on-disk caches
  upstox-fetch login      Run the OAuth login flow
  upstox-fetch keys       Print built-in NSE instrument keys
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date as _date, datetime
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table

from upstox_historical.config import configure_logging, get_settings
from upstox_historical.fetcher import HistoricalFetcher
from upstox_historical.instruments import NSE, list_keys
from upstox_historical.models import Interval

app = typer.Typer(
    name="upstox-fetch",
    help="Fetch, update, chart, and validate historical OHLCV data from Upstox v2.",
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


# ── Rich progress factory (shared by fetch + batch) ──────────────────

def _make_progress() -> Progress:
    """Standard progress layout: spinner | description | bar | M/N | elapsed | ETA."""
    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=40),
        MofNCompleteColumn(),
        TextColumn("•"),
        TimeElapsedColumn(),
        TextColumn("•"),
        TimeRemainingColumn(),
        console=console,
        transient=False,
    )


# ── fetch (async with progress bar) ──────────────────────────────────

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
    ] = "parquet",
    preview: Annotated[
        bool,
        typer.Option("--preview/--no-preview", help="Print the first 10 rows."),
    ] = True,
    sync: Annotated[
        bool,
        typer.Option("--sync", help="Use sync fetcher (slower, for debugging)."),
    ] = False,
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
    validate: Annotated[
        bool,
        typer.Option("--validate/--no-validate", help="Run quality checks after fetch."),
    ] = True,
) -> None:
    """Fetch historical OHLCV candles and save to disk."""
    from upstox_historical.fetcher import _CHUNK_SIZE, _FETCH_AS, _date_chunks

    fetch_as = _FETCH_AS.get(interval.value, interval.value)
    chunk_size = _CHUNK_SIZE[fetch_as]
    chunks = list(_date_chunks(from_date, to_date, chunk_size))
    n_chunks = len(chunks)

    console.print(
        f"[dim]Instrument:[/] [cyan]{instrument_key}[/]  "
        f"[dim]Interval:[/] [cyan]{interval.value}[/]  "
        f"[dim]Chunks:[/] [yellow]{n_chunks}[/]  "
        f"[dim]Mode:[/] [cyan]{'sync' if sync else 'async'}[/]"
    )
    if nse_enrich:
        console.print(
            f"[dim]NSE enrichment:[/] [green]on[/]  "
            f"[dim]Symbol:[/] [cyan]{symbol or 'auto'}[/]  "
            f"[dim]Series:[/] [cyan]{series or 'auto'}[/]"
        )

    if sync:
        df = _run_sync_fetch(
            instrument_key, interval, from_date, to_date,
            nse_enrich, symbol, series, n_chunks,
        )
    else:
        df = _run_async_fetch(
            instrument_key, interval, from_date, to_date,
            nse_enrich, symbol, series, n_chunks,
        )

    if df is None or df.empty:
        console.print("[yellow]No data returned.[/]")
        raise typer.Exit(1)

    # Save
    saved = HistoricalFetcher._save(
        df, instrument_key, interval, from_date, to_date, out_dir, fmt,
    )
    console.print(f"[bold green]✓[/] Saved → [cyan]{saved}[/]  "
                  f"([dim]{len(df):,} rows[/])")

    if validate:
        _run_validation(df, quiet=False)

    if preview:
        _render_table(instrument_key, df.head(10))


def _run_sync_fetch(
    instrument_key: str, interval: Interval, from_date: str, to_date: str,
    nse_enrich: bool, symbol: Optional[str], series: Optional[str], n_chunks: int,
):
    """Sync path using HistoricalFetcher, with a Progress bar for enrichment."""
    fetcher = HistoricalFetcher()

    if nse_enrich:
        with _make_progress() as progress:
            fetch_task = progress.add_task(
                f"[cyan]Fetching[/] {interval.value} (sync)", total=n_chunks,
            )
            # Sync path doesn't emit chunk callbacks — just show spinner text.
            # We complete it when the blocking call returns.
            enrich_task = {"id": None}

            def enrich_cb(done: int, total: int, cur_date) -> None:
                if enrich_task["id"] is None:
                    enrich_task["id"] = progress.add_task(
                        "[magenta]NSE enrichment[/]", total=total,
                    )
                progress.update(enrich_task["id"], completed=done)

            df = fetcher.fetch(
                instrument_key=instrument_key,
                interval=interval,
                from_date=from_date,
                to_date=to_date,
                nse_enrich=nse_enrich,
                symbol=symbol,
                series=series,
                enrich_progress_cb=enrich_cb,
            )
            progress.update(fetch_task, completed=n_chunks)
            return df
    else:
        with console.status(f"[bold green]Fetching {n_chunks} chunk(s) …"):
            return fetcher.fetch(
                instrument_key=instrument_key,
                interval=interval,
                from_date=from_date,
                to_date=to_date,
                nse_enrich=nse_enrich,
                symbol=symbol,
                series=series,
            )


def _run_async_fetch(
    instrument_key: str, interval: Interval, from_date: str, to_date: str,
    nse_enrich: bool, symbol: Optional[str], series: Optional[str], n_chunks: int,
):
    """Async path with Rich progress bars for chunks AND (optionally) enrichment."""
    from upstox_historical.async_fetcher import AsyncHistoricalFetcher

    async def _do() -> object:
        fetcher = AsyncHistoricalFetcher()
        with _make_progress() as progress:
            fetch_task = progress.add_task(
                f"[cyan]Fetching[/] {interval.value}", total=n_chunks,
            )

            def chunk_cb(done: int, total: int, cf, ct) -> None:
                progress.update(fetch_task, completed=done)

            # Enrichment task is lazily created on first Bhav Copy lookup —
            # we don't know how many trading dates there are until the raw
            # candles come back.
            enrich_task = {"id": None}

            def enrich_cb(done: int, total: int, cur_date) -> None:
                if enrich_task["id"] is None:
                    enrich_task["id"] = progress.add_task(
                        "[magenta]NSE enrichment[/]", total=total,
                    )
                progress.update(enrich_task["id"], completed=done)

            df = await fetcher.fetch(
                instrument_key=instrument_key,
                interval=interval,
                from_date=from_date,
                to_date=to_date,
                nse_enrich=nse_enrich,
                symbol=symbol,
                series=series,
                progress_cb=chunk_cb,
                enrich_progress_cb=enrich_cb if nse_enrich else None,
            )
            progress.update(fetch_task, completed=n_chunks)
            return df

    return asyncio.run(_do())


# ── batch ────────────────────────────────────────────────────────────

@app.command()
def batch(
    symbols: Annotated[
        str,
        typer.Option(
            "--symbols",
            help='Comma-separated NSE ticker symbols, e.g. "RELIANCE,TCS,INFY". '
                 'Resolved via built-in instrument keys where available.',
        ),
    ],
    interval: Annotated[Interval, typer.Option("--interval", "-i")] = Interval.D1,
    from_date: Annotated[str, typer.Option("--from", "-f")] = "2024-01-01",
    to_date: Annotated[str, typer.Option("--to", "-t")] = "2024-12-31",
    out_dir: Annotated[Path, typer.Option("--out-dir", "-o")] = Path("./data"),
    fmt: Annotated[str, typer.Option("--format")] = "parquet",
    nse_enrich: Annotated[bool, typer.Option("--nse-enrich")] = False,
    series: Annotated[str, typer.Option("--series")] = "EQ",
    validate: Annotated[bool, typer.Option("--validate/--no-validate")] = True,
) -> None:
    """Fetch many instruments concurrently."""
    from upstox_historical.async_fetcher import AsyncHistoricalFetcher

    tickers = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not tickers:
        console.print("[red]No symbols provided.[/]")
        raise typer.Exit(1)

    # Resolve tickers → instrument_keys
    resolved: list[tuple[str, str]] = []  # (instrument_key, ticker)
    unknown: list[str] = []
    for t in tickers:
        if hasattr(NSE, t):
            resolved.append((getattr(NSE, t), t))
        else:
            # Assume NSE_EQ|TICKER form for unrecognised names
            resolved.append((f"NSE_EQ|{t}", t))
            unknown.append(t)

    if unknown:
        console.print(
            f"[yellow]Note:[/] {len(unknown)} symbol(s) not in built-ins, assuming NSE_EQ: "
            f"{', '.join(unknown)}"
        )

    console.print(
        f"[dim]Batch:[/] [cyan]{len(resolved)}[/] instruments  "
        f"[dim]Interval:[/] [cyan]{interval.value}[/]  "
        f"[dim]Range:[/] [cyan]{from_date} → {to_date}[/]"
    )

    symbols_map = {key: ticker for key, ticker in resolved}
    keys = [key for key, _ in resolved]

    async def _do() -> dict:
        fetcher = AsyncHistoricalFetcher()
        with _make_progress() as progress:
            task = progress.add_task(
                "[cyan]Fetching instruments[/]", total=len(keys),
            )

            def cb(ikey: str, done: int, total: int) -> None:
                ticker = symbols_map.get(ikey, ikey)
                progress.update(task, completed=done, description=f"[cyan]✓ {ticker}[/]")

            return await fetcher.fetch_many(
                instrument_keys=keys,
                interval=interval,
                from_date=from_date,
                to_date=to_date,
                nse_enrich=nse_enrich,
                symbols=symbols_map,
                series=series,
                progress_cb=cb,
            )

    results = asyncio.run(_do())

    # Save + summary
    table = Table(title="Batch fetch results", show_lines=False)
    table.add_column("Symbol", style="cyan")
    table.add_column("Rows", justify="right")
    table.add_column("File")
    table.add_column("Status")

    for key, df in results.items():
        ticker = symbols_map[key]
        if df.empty:
            table.add_row(ticker, "0", "-", "[red]failed / no data[/]")
            continue
        path = HistoricalFetcher._save(df, key, interval, from_date, to_date, out_dir, fmt)
        status = "[green]ok[/]"
        if validate:
            from upstox_historical.validation import validate as _validate
            report = _validate(df, check_gaps=True)
            if report.errors:
                status = f"[red]{len(report.errors)} errors[/]"
            elif report.warnings:
                status = f"[yellow]{len(report.warnings)} warnings[/]"
        table.add_row(ticker, f"{len(df):,}", path.name, status)

    console.print(table)


# ── update ───────────────────────────────────────────────────────────

@app.command()
def update(
    path: Annotated[Path, typer.Argument(help="Path to existing .csv or .parquet.")],
    until: Annotated[
        Optional[str],
        typer.Option("--until", help="End date YYYY-MM-DD (default: today)."),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Show what would be fetched, don't run."),
    ] = False,
    validate: Annotated[bool, typer.Option("--validate/--no-validate")] = True,
) -> None:
    """Incrementally update an existing saved file to today (or --until)."""
    from upstox_historical.updater import plan_update, update as _update

    if not path.exists():
        console.print(f"[red]File not found:[/] {path}")
        raise typer.Exit(1)

    plan = plan_update(path, until=until)

    console.print(
        f"[dim]File:[/] [cyan]{plan.path.name}[/]\n"
        f"[dim]Existing:[/] [cyan]{plan.existing_from} → {plan.existing_to}[/]  "
        f"[dim]Interval:[/] [cyan]{plan.interval}[/]"
    )
    if plan.skip:
        console.print("[green]✓[/] Already up-to-date.")
        raise typer.Exit()

    console.print(f"[dim]Will fetch:[/] [yellow]{plan.new_from} → {plan.new_to}[/]")
    if dry_run:
        console.print("[yellow]Dry run — no fetch performed.[/]")
        raise typer.Exit()

    with console.status("[bold green]Updating …"):
        result = asyncio.run(_update(path, until=until))

    console.print(f"[bold green]✓[/] Updated → [cyan]{result.path}[/]")

    if validate:
        import pandas as pd
        df = pd.read_parquet(result.path) if result.path.suffix == ".parquet" \
             else pd.read_csv(result.path)
        _run_validation(df, quiet=False)


# ── intraday ─────────────────────────────────────────────────────────

@app.command()
def intraday(
    instrument_key: Annotated[str, typer.Argument()],
    interval: Annotated[Interval, typer.Option("--interval", "-i")] = Interval.I1M,
    out_dir: Annotated[Path, typer.Option("--out-dir", "-o")] = Path("./data"),
    fmt: Annotated[str, typer.Option("--format")] = "csv",
    preview: Annotated[bool, typer.Option("--preview/--no-preview")] = True,
    nse_enrich: Annotated[bool, typer.Option("--nse-enrich")] = False,
    symbol: Annotated[Optional[str], typer.Option("--symbol", "-s")] = None,
    series: Annotated[Optional[str], typer.Option("--series")] = None,
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

    today = _date.today().isoformat()
    path = fetcher._save(df, instrument_key, interval, today, today, out_dir, fmt)
    console.print(f"[bold green]✓[/] Saved → [cyan]{path}[/]")
    if preview:
        _render_table(instrument_key, df.head(10))


# ── plot ─────────────────────────────────────────────────────────────

@app.command()
def plot(
    path: Annotated[Path, typer.Argument(help="Saved .csv or .parquet.")],
    indicators: Annotated[
        str,
        typer.Option(
            "--indicators", "-I",
            help='Comma-separated, e.g. "sma20,sma50,bb20,rsi14,macd,vwap_overlay".',
        ),
    ] = "",
    out: Annotated[Optional[Path], typer.Option("--out", "-o")] = None,
    open_: Annotated[
        bool,
        typer.Option("--open", help="Open HTML in default browser after writing."),
    ] = False,
    title: Annotated[Optional[str], typer.Option("--title")] = None,
) -> None:
    """Render an interactive Plotly chart of a saved candle file."""
    from upstox_historical.plotting import plot_candles

    if not path.exists():
        console.print(f"[red]File not found:[/] {path}")
        raise typer.Exit(1)

    inds = [s.strip() for s in indicators.split(",") if s.strip()]
    try:
        out_path = plot_candles(
            path, indicators=inds, out=out, title=title, open_browser=open_,
        )
    except ImportError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from exc
    except ValueError as exc:
        console.print(f"[red]Error:[/] {exc}")
        raise typer.Exit(1) from exc

    console.print(f"[bold green]✓[/] Wrote → [cyan]{out_path}[/]")
    if inds:
        console.print(f"[dim]Indicators:[/] {', '.join(inds)}")


# ── validate ─────────────────────────────────────────────────────────

@app.command()
def validate(
    path: Annotated[Path, typer.Argument(help="Saved .csv or .parquet.")],
) -> None:
    """Run quality checks on a saved candle file."""
    import pandas as pd

    if not path.exists():
        console.print(f"[red]File not found:[/] {path}")
        raise typer.Exit(1)

    df = pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path)
    _run_validation(df, quiet=False)


def _run_validation(df, quiet: bool = False) -> None:
    """Shared validation output (used by fetch, update, validate)."""
    from upstox_historical.validation import validate as _validate

    report = _validate(df, check_gaps=True)

    if not quiet:
        if report.errors:
            console.print(f"[red bold]✗ {len(report.errors)} error(s)[/]")
            for e in report.errors:
                console.print(f"  [red]•[/] {e}")
        elif report.warnings:
            console.print(f"[yellow]⚠ {len(report.warnings)} warning(s)[/]")
            for w in report.warnings:
                console.print(f"  [yellow]•[/] {w}")
        else:
            console.print(f"[green]✓ Validation clean[/] "
                          f"([dim]{report.n_rows:,} rows[/])")


# ── find (interactive instrument search) ─────────────────────────────

@app.command()
def find(
    query: Annotated[Optional[str], typer.Argument(help="Optional initial query.")] = None,
    non_interactive: Annotated[
        bool,
        typer.Option("--non-interactive", help="Print results only, no prompt."),
    ] = False,
) -> None:
    """Interactive instrument search. Copies selected key to clipboard."""
    from upstox_historical.search import interactive_find, search_instruments

    if non_interactive:
        if not query:
            console.print("[red]--non-interactive requires a query.[/]")
            raise typer.Exit(1)
        results = search_instruments(query)
        if not results:
            console.print(f"[yellow]No results for {query!r}.[/]")
            raise typer.Exit()
        table = Table(title=f"Results for {query!r}")
        table.add_column("Symbol", style="cyan")
        table.add_column("Exchange")
        table.add_column("Name")
        table.add_column("Type")
        table.add_column("Instrument key", style="dim")
        for r in results:
            table.add_row(
                r.trading_symbol, r.exchange, r.name,
                r.instrument_type, r.instrument_key,
            )
        console.print(table)
        return

    chosen = interactive_find(default_query=query)
    if chosen is None:
        console.print("[dim]Cancelled.[/]")
    else:
        console.print(f"[bold green]Selected:[/] [cyan]{chosen}[/]")


# ── cache (inspect + clear) ──────────────────────────────────────────

@app.command()
def cache(
    action: Annotated[
        str,
        typer.Argument(help="stats | clear-bhav | clear-checkpoints | clear-all"),
    ] = "stats",
) -> None:
    """Inspect or clear the on-disk caches."""
    from upstox_historical.cache import (
        bhav_cache_clear,
        bhav_cache_stats,
        cache_root,
        checkpoint_stats,
    )
    import shutil

    root = cache_root()
    if action == "stats":
        bs = bhav_cache_stats()
        cs = checkpoint_stats()
        table = Table(title=f"Cache: {root}")
        table.add_column("Cache")
        table.add_column("Stat")
        table.add_column("Value", justify="right")
        table.add_row("bhav", "parquet files", str(bs["hits_available"]))
        table.add_row("bhav", "miss markers", str(bs["miss_markers"]))
        table.add_row("bhav", "size (MB)", str(bs["size_mb"]))
        table.add_row("checkpoints", "active jobs", str(cs["active_jobs"]))
        table.add_row("checkpoints", "total chunks", str(cs["total_chunks"]))
        table.add_row("checkpoints", "size (MB)", str(cs["size_mb"]))
        console.print(table)
    elif action == "clear-bhav":
        n = bhav_cache_clear()
        console.print(f"[green]✓[/] Removed [cyan]{n}[/] Bhav cache files.")
    elif action == "clear-checkpoints":
        ckpt_dir = root / "checkpoints"
        if ckpt_dir.exists():
            shutil.rmtree(ckpt_dir, ignore_errors=True)
            console.print(f"[green]✓[/] Cleared checkpoints.")
        else:
            console.print("[dim]No checkpoints to clear.[/]")
    elif action == "clear-all":
        if root.exists():
            shutil.rmtree(root, ignore_errors=True)
            console.print(f"[green]✓[/] Cleared all caches at {root}.")
        else:
            console.print("[dim]Nothing to clear.[/]")
    else:
        console.print(f"[red]Unknown action:[/] {action}")
        console.print("[dim]Valid: stats | clear-bhav | clear-checkpoints | clear-all[/]")
        raise typer.Exit(1)


# ── login ────────────────────────────────────────────────────────────

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


# ── keys ─────────────────────────────────────────────────────────────

@app.command()
def keys() -> None:
    """Print all built-in NSE instrument keys."""
    list_keys()


# ── helpers ──────────────────────────────────────────────────────────

def _render_table(title: str, df) -> None:
    table = Table(title=f"Preview — {title}", show_lines=False)
    ts_col = "timestamp" if "timestamp" in df.columns else ("Date" if "Date" in df.columns else None)
    for col in df.columns:
        style = "cyan" if col == ts_col else "white"
        table.add_column(str(col), style=style)
    for _, row in df.iterrows():
        table.add_row(*[str(v) for v in row])
    console.print(table)


if __name__ == "__main__":
    app()
