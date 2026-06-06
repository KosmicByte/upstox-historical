"""
config.py — centralised settings loaded from .env

Token priority
--------------
analytics_token (UPSTOX_ANALYTICS_TOKEN) — 1-year validity, preferred for all
    Market Data, Market Quote, Market Information, Historical, and WebSocket APIs.
    Generate once at: account.upstox.com/developer/apps → Analytics tab.

access_token (UPSTOX_ACCESS_TOKEN) — daily OAuth token, required only for
    order placement, Trade P&L, and portfolio APIs.

Any method that accepts a token will prefer analytics_token when present,
falling back to access_token automatically.
"""
from __future__ import annotations

import logging
from pathlib import Path

from dotenv import load_dotenv
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict  # type: ignore[import]

# Load .env from project root (two levels up from this file when inside src/)
_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(_ENV_PATH, override=False)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="UPSTOX_", case_sensitive=False)

    # ── Auth ──────────────────────────────────────────────────────────
    api_key: str = ""
    api_secret: str = ""

    # Daily OAuth token — required for orders, Trade P&L, portfolio APIs
    access_token: str = ""

    # 1-year Analytics Token — preferred for all market data + WebSocket APIs
    # Generate at: account.upstox.com/developer/apps → Analytics tab
    analytics_token: str = ""

    # ── Storage ───────────────────────────────────────────────────────
    output_dir: str = "./data"

    # ── Logging ───────────────────────────────────────────────────────
    log_level: str = "INFO"

    # ── API base URLs (not user-configurable) ─────────────────────────
    base_url: str = "https://api.upstox.com/v2"
    base_url_v3: str = "https://api.upstox.com/v3"

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in valid:
            raise ValueError(f"log_level must be one of {valid}")
        return upper

    # ── Helpers ───────────────────────────────────────────────────────

    def best_token(self) -> str:
        """
        Return the best available token for market data APIs.
        Prefers analytics_token (1-year) over access_token (daily).
        Raises if neither is set.
        """
        token = self.analytics_token or self.access_token
        if not token:
            raise ValueError(
                "No token found. Set UPSTOX_ANALYTICS_TOKEN (preferred, 1-year) "
                "or UPSTOX_ACCESS_TOKEN in your .env file."
            )
        return token

    def has_analytics_token(self) -> bool:
        """True when the long-lived Analytics Token is available."""
        return bool(self.analytics_token)


def get_settings() -> Settings:
    return Settings()


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
