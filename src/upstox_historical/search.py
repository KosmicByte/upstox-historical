"""
search.py — Interactive instrument search and selection.

Provides two paths:

1. ``search_instruments(query)`` — programmatic function returning a list of
   ``InstrumentSearchResult`` objects, used by CLI and scripts.

2. ``interactive_find()`` — a CLI-facing routine that prompts for a query,
   shows results as a selectable list, and returns the chosen instrument key
   (or prints/copies it).

The interactive path uses ``questionary`` for a clean, arrow-key-navigable
prompt that works in any ANSI terminal.
"""
from __future__ import annotations

import logging
from typing import Optional

from upstox_historical.client import UpstoxAPIError, UpstoxClient
from upstox_historical.models import InstrumentSearchResult

logger = logging.getLogger(__name__)


def search_instruments(
    query: str,
    client: UpstoxClient | None = None,
    max_results: int = 25,
) -> list[InstrumentSearchResult]:
    """
    Search the Upstox instruments endpoint and return typed results.

    Parameters
    ----------
    query : str
        Free-text search (e.g. "reliance", "INE002", "NIFTY").
    client : UpstoxClient, optional
        Re-use an existing client. A new one is constructed if omitted.
    max_results : int
        Trim the returned list.

    Returns
    -------
    list[InstrumentSearchResult]
    """
    own = client is None
    client = client or UpstoxClient()
    try:
        raw = client.search_instruments(query)
    finally:
        if own:
            client.close()

    data = raw.get("data", []) if isinstance(raw, dict) else []
    parsed: list[InstrumentSearchResult] = []
    for item in data[:max_results]:
        try:
            parsed.append(InstrumentSearchResult.model_validate(item))
        except Exception as exc:
            logger.debug("Skipped unparseable instrument: %s (%s)", item, exc)
    return parsed


def interactive_find(
    default_query: Optional[str] = None,
    client: UpstoxClient | None = None,
    copy_to_clipboard: bool = True,
) -> Optional[str]:
    """
    Prompt for a search query, display results, and return the chosen
    instrument key (or None if cancelled).

    Parameters
    ----------
    default_query : str, optional
        Pre-fill the query prompt.
    client : UpstoxClient, optional
    copy_to_clipboard : bool
        Attempt to copy the selected key to the clipboard (falls back
        silently if no clipboard backend is available).

    Returns
    -------
    str | None
        The chosen instrument_key, or None if the user cancelled.
    """
    # Lazy import so `search_instruments` works in environments without questionary
    try:
        import questionary
    except ImportError as exc:
        raise ImportError(
            "questionary is required for interactive search. "
            "Install with: uv add questionary"
        ) from exc

    query = default_query or questionary.text(
        "Search instruments:",
        validate=lambda s: True if s and s.strip() else "Enter a query",
    ).ask()

    if not query:
        return None

    own_client = client is None
    client = client or UpstoxClient()

    try:
        while True:
            try:
                results = search_instruments(query, client=client, max_results=25)
            except UpstoxAPIError as exc:
                questionary.print(f"Search failed: {exc}", style="fg:#cc0000")
                return None

            if not results:
                questionary.print(
                    f"No results for {query!r}.", style="fg:#cc7a00",
                )
                again = questionary.confirm("Search again?", default=True).ask()
                if not again:
                    return None
                query = questionary.text("Search instruments:").ask()
                if not query:
                    return None
                continue

            choices = [
                questionary.Choice(
                    title=_format_choice(r),
                    value=r.instrument_key,
                )
                for r in results
            ]
            choices.append(questionary.Choice(title="— search again —", value="__AGAIN__"))
            choices.append(questionary.Choice(title="— cancel —", value=None))

            chosen = questionary.select(
                f"Results for {query!r} (↑↓ to move, Enter to select):",
                choices=choices,
                use_shortcuts=False,
            ).ask()

            if chosen is None:
                return None
            if chosen == "__AGAIN__":
                query = questionary.text("Search instruments:").ask()
                if not query:
                    return None
                continue

            # Selection made
            if copy_to_clipboard:
                _try_clipboard(chosen)
            return chosen
    finally:
        if own_client:
            client.close()


# ── helpers ──────────────────────────────────────────────────────────

def _format_choice(r: InstrumentSearchResult) -> str:
    """Render one search result as a selectable line."""
    # Compose: SYMBOL  (exchange)  — name  [key]
    line = f"{r.trading_symbol:<14}  {r.exchange:<10}  {r.name}"
    if r.instrument_type not in ("EQ", "INDEX"):
        line += f"  · {r.instrument_type}"
    if r.expiry:
        line += f"  exp:{r.expiry}"
    line += f"    [{r.instrument_key}]"
    return line


def _try_clipboard(text: str) -> None:
    """
    Copy `text` to the system clipboard if possible.
    Tries pyperclip first (cross-platform); swallows failures silently.
    """
    try:
        import pyperclip  # type: ignore[import]
        pyperclip.copy(text)
        logger.info("Copied to clipboard: %s", text)
    except Exception:
        # No clipboard available (headless server, no xclip, etc.) — just log
        logger.debug("Clipboard copy unavailable; selection: %s", text)
