"""
tests/test_fetcher.py — unit tests (no real API calls).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest

from upstox_historical.fetcher import HistoricalFetcher
from upstox_historical.models import Interval


# ── fixtures ─────────────────────────────────────────────────────────

SAMPLE_RESPONSE = {
    "status": "success",
    "data": {
        "candles": [
            ["2024-01-02T09:15:00+05:30", 21800.0, 21900.0, 21750.0, 21850.0, 123456, 0],
            ["2024-01-03T09:15:00+05:30", 21850.0, 21950.0, 21800.0, 21920.0, 234567, 0],
        ]
    },
}

# Upstox returns candles as plain lists, not dicts — the model handles both,
# but let's also test with dict format (model_validate path).
SAMPLE_RESPONSE_DICT = {
    "status": "success",
    "data": {
        "candles": [
            {
                "timestamp": "2024-01-02T09:15:00+05:30",
                "open": 21800.0, "high": 21900.0, "low": 21750.0,
                "close": 21850.0, "volume": 123456, "open_interest": 0,
            }
        ]
    },
}


@pytest.fixture()
def mock_client() -> MagicMock:
    client = MagicMock()
    client.get_historical_candles.return_value = SAMPLE_RESPONSE_DICT
    client.get_intraday_candles.return_value = SAMPLE_RESPONSE_DICT
    return client


# ── tests ─────────────────────────────────────────────────────────────

class TestHistoricalFetcher:
    def test_fetch_returns_dataframe(self, mock_client: MagicMock) -> None:
        fetcher = HistoricalFetcher(client=mock_client)
        df = fetcher.fetch("NSE_INDEX|Nifty 50", Interval.D1, "2024-01-01", "2024-01-31")
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 1
        assert "close" in df.columns
        assert "timestamp" in df.columns

    def test_fetch_intraday(self, mock_client: MagicMock) -> None:
        fetcher = HistoricalFetcher(client=mock_client)
        df = fetcher.fetch_intraday("NSE_INDEX|Nifty 50", Interval.I1M)
        assert not df.empty

    def test_fetch_and_save_csv(self, mock_client: MagicMock, tmp_path) -> None:
        fetcher = HistoricalFetcher(client=mock_client)
        path = fetcher.fetch_and_save(
            "NSE_INDEX|Nifty 50", Interval.D1,
            "2024-01-01", "2024-01-31",
            out_dir=tmp_path, fmt="csv",
        )
        assert path.exists()
        assert path.suffix == ".csv"
        saved = pd.read_csv(path)
        assert len(saved) == 1

    def test_fetch_and_save_parquet(self, mock_client: MagicMock, tmp_path) -> None:
        fetcher = HistoricalFetcher(client=mock_client)
        path = fetcher.fetch_and_save(
            "NSE_INDEX|Nifty 50", Interval.D1,
            "2024-01-01", "2024-01-31",
            out_dir=tmp_path, fmt="parquet",
        )
        assert path.suffix == ".parquet"

    def test_empty_candles_returns_empty_df(self, mock_client: MagicMock) -> None:
        mock_client.get_historical_candles.return_value = {
            "status": "success",
            "data": {"candles": []},
        }
        fetcher = HistoricalFetcher(client=mock_client)
        df = fetcher.fetch("NSE_INDEX|Nifty 50", Interval.D1, "2024-01-01", "2024-01-31")
        assert df.empty


class TestModels:
    def test_interval_enum_values(self) -> None:
        assert Interval.D1.value == "day"
        assert Interval.I1M.value == "1minute"

    def test_historical_response_bad_status(self) -> None:
        from pydantic import ValidationError
        from upstox_historical.models import HistoricalResponse
        with pytest.raises(ValidationError):
            HistoricalResponse.model_validate({"status": "error", "data": {"candles": []}})
