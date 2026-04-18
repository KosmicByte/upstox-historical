"""
auth.py — Upstox OAuth 2.0 helper.

The Upstox API uses a standard OAuth Authorization-Code flow.
This module helps you:
  1. Generate the login URL.
  2. Exchange the auth code for an access token.
  3. Print / save the token so you can paste it into .env.

HOW TO USE (one-time setup)
────────────────────────────
Run from the project root:

    uv run python -m upstox_historical.auth

Or call directly:

    from upstox_historical.auth import get_auth_url, exchange_code

See README § Authentication for full walkthrough.
"""
from __future__ import annotations

import logging
import webbrowser
from urllib.parse import urlencode

import httpx

from upstox_historical.config import get_settings

logger = logging.getLogger(__name__)

_AUTH_BASE = "https://api.upstox.com/v2/login/authorization/dialog"
_TOKEN_URL = "https://api.upstox.com/v2/login/authorization/token"


def get_auth_url(redirect_uri: str = "http://localhost:8000/callback") -> str:
    """
    Build the Upstox OAuth login URL.

    Parameters
    ----------
    redirect_uri : str
        Must exactly match the redirect URI registered in your Upstox app.

    Returns
    -------
    str
        URL to open in a browser.
    """
    s = get_settings()
    params = {
        "response_type": "code",
        "client_id": s.api_key,
        "redirect_uri": redirect_uri,
    }
    url = f"{_AUTH_BASE}?{urlencode(params)}"
    return url


def exchange_code(
    auth_code: str,
    redirect_uri: str = "http://localhost:8000/callback",
) -> str:
    """
    Exchange an authorization code for an access token.

    Parameters
    ----------
    auth_code : str
        The ``code`` query-parameter from the redirect URL after login.

    Returns
    -------
    str
        The access token string.
    """
    s = get_settings()
    resp = httpx.post(
        _TOKEN_URL,
        data={
            "code": auth_code,
            "client_id": s.api_key,
            "client_secret": s.api_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        headers={"accept": "application/json"},
        timeout=15.0,
    )
    resp.raise_for_status()
    data = resp.json()
    token: str = data["access_token"]
    return token


def interactive_login(redirect_uri: str = "http://localhost:8000/callback") -> str:
    """
    Interactive CLI flow:
    1. Opens the Upstox login URL in your browser.
    2. Prompts for the auth code from the redirect URL.
    3. Returns the access token.
    """
    url = get_auth_url(redirect_uri)
    print("\n── Upstox OAuth Login ─────────────────────────────────────────")
    print(f"Opening:\n  {url}\n")
    webbrowser.open(url)

    print("After login, Upstox will redirect to something like:")
    print(f"  {redirect_uri}?code=XXXXXX\n")
    auth_code = input("Paste the 'code' value from the URL: ").strip()

    token = exchange_code(auth_code, redirect_uri)
    print("\n✓ Access token obtained!")
    print(f"\nAdd this to your .env file:\n  UPSTOX_ACCESS_TOKEN={token}\n")
    return token


if __name__ == "__main__":
    import sys
    logging.basicConfig(level="INFO")
    redirect = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000/callback"
    interactive_login(redirect)
