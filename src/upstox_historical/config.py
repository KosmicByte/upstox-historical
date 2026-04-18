"""
config.py — centralised settings loaded from .env
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict  # type: ignore[import]

# Load .env from project root (two levels up from this file)
_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(_ENV_PATH, override=False)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="UPSTOX_", case_sensitive=False)

    api_key: str = ""
    api_secret: str = ""
    access_token: str = ""

    output_dir: str = "./data"
    log_level: str = "INFO"

    # Upstox v2 base URL (not configurable by user, but centralised here)
    base_url: str = "https://api.upstox.com/v2"

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in valid:
            raise ValueError(f"log_level must be one of {valid}")
        return upper


def get_settings() -> Settings:
    return Settings()


def configure_logging(level: str = "INFO") -> None:
    """
    Configure root logging.

    Silences noisy third-party loggers (httpx, httpcore, urllib3) unless
    DEBUG is explicitly requested via ``--verbose`` / ``-v``. Without this,
    httpx emits one INFO line per HTTP request — fine for debugging, but
    visually overwhelming for normal multi-chunk fetches.
    """
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Quiet third-party per-request logs at INFO level.
    # --verbose still surfaces everything because it sets level=DEBUG globally.
    if level != "DEBUG":
        for noisy in ("httpx", "httpcore", "urllib3"):
            logging.getLogger(noisy).setLevel(logging.WARNING)
