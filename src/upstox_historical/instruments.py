"""
instruments.py — Pre-defined Upstox instrument_key constants for common
                 NSE indices and equities so you don't have to look them up.

Usage::

    from upstox_historical.instruments import NSE
    key = NSE.NIFTY_50        # "NSE_INDEX|Nifty 50"
    key = NSE.BANKNIFTY       # "NSE_INDEX|Nifty Bank"
    key = NSE.RELIANCE        # "NSE_EQ|INE002A01018"
"""
from __future__ import annotations


class NSE:
    # ── Indices ──────────────────────────────────
    NIFTY_50        = "NSE_INDEX|Nifty 50"
    BANKNIFTY       = "NSE_INDEX|Nifty Bank"
    FINNIFTY        = "NSE_INDEX|Nifty Fin Service"
    MIDCAP_NIFTY    = "NSE_INDEX|NIFTY MID SELECT"
    SENSEX          = "BSE_INDEX|SENSEX"
    INDIA_VIX       = "NSE_INDEX|India VIX"

    # ── Large-cap equities (ISIN-based instrument keys) ──
    RELIANCE        = "NSE_EQ|INE002A01018"
    TCS             = "NSE_EQ|INE467B01029"
    INFY            = "NSE_EQ|INE009A01021"
    HDFCBANK        = "NSE_EQ|INE040A01034"
    ICICIBANK       = "NSE_EQ|INE090A01021"
    SBIN            = "NSE_EQ|INE062A01020"
    WIPRO           = "NSE_EQ|INE075A01022"
    TATAMOTORS      = "NSE_EQ|INE155A01022"
    AXISBANK        = "NSE_EQ|INE238A01034"
    BAJFINANCE      = "NSE_EQ|INE296A01024"
    ADANIPORTS      = "NSE_EQ|INE742F01042"
    LTIM            = "NSE_EQ|INE214T01019"
    MARUTI          = "NSE_EQ|INE585B01010"
    SUNPHARMA       = "NSE_EQ|INE044A01036"
    TITAN           = "NSE_EQ|INE280A01028"
    ULTRACEMCO      = "NSE_EQ|INE481G01011"
    NESTLEIND       = "NSE_EQ|INE239A01016"
    POWERGRID       = "NSE_EQ|INE752E01010"
    NTPC            = "NSE_EQ|INE733E01010"
    ONGC            = "NSE_EQ|INE213A01029"


# Helper to print all known keys
def list_keys() -> None:
    import inspect
    members = inspect.getmembers(NSE, lambda v: isinstance(v, str))
    for name, key in members:
        print(f"  {name:<20} → {key}")


if __name__ == "__main__":
    list_keys()
