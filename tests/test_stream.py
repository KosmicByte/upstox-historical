"""
test_stream.py — offline tests for the WebSocket stream layer.

Two groups:
  1. Protobuf decoding — builds real FeedResponse messages from the
     generated proto and verifies _parse_feed_response() decodes them.
  2. Subscription message construction + LiveStream validation logic.

No live market or network connection is used.
"""
from __future__ import annotations

import json

import pytest

from upstox_historical.stream import (
    LiveStream,
    Tick,
    Depth,
    OHLC,
    _parse_feed_response,
    _build_subscription_message,
    _build_unsubscribe_message,
)
from upstox_historical.proto import MarketDataFeed_pb2 as pb


# ── Helpers to build proto messages ───────────────────────────────────

def _make_ltpc_response(key: str, ltp: float, cp: float = 0.0) -> bytes:
    """Build a FeedResponse with a single LTPC feed and serialise it."""
    resp = pb.FeedResponse()
    resp.type = pb.Type.live_feed
    feed = resp.feeds[key]
    feed.ltpc.ltp = ltp
    feed.ltpc.ltt = 1700000000000
    feed.ltpc.ltq = 50
    feed.ltpc.cp = cp
    return resp.SerializeToString()


def _make_index_full_response(key: str, ltp: float) -> bytes:
    """Build a FeedResponse with an IndexFullFeed (LTPC + OHLC)."""
    resp = pb.FeedResponse()
    resp.type = pb.Type.live_feed
    feed = resp.feeds[key]
    iff = feed.fullFeed.indexFF
    iff.ltpc.ltp = ltp
    iff.ltpc.cp = ltp - 10
    ohlc = iff.marketOHLC.ohlc.add()
    ohlc.interval = "1d"
    ohlc.open = ltp - 20
    ohlc.high = ltp + 5
    ohlc.low = ltp - 25
    ohlc.close = ltp
    ohlc.vol = 0
    ohlc.ts = 1700000000000
    return resp.SerializeToString()


def _make_market_full_response(key: str, ltp: float) -> bytes:
    """Build a FeedResponse with a MarketFullFeed (depth + volume + OI)."""
    resp = pb.FeedResponse()
    resp.type = pb.Type.live_feed
    feed = resp.feeds[key]
    mff = feed.fullFeed.marketFF
    mff.ltpc.ltp = ltp
    mff.ltpc.cp = ltp - 1
    mff.vtt = 123456
    mff.oi = 7890.0
    mff.atp = ltp - 0.5
    mff.iv = 0.18
    mff.tbq = 1000.0
    mff.tsq = 800.0

    # Two depth levels
    q1 = mff.marketLevel.bidAskQuote.add()
    q1.bidP, q1.bidQ = ltp - 0.5, 100
    q1.askP, q1.askQ = ltp + 0.5, 120
    q2 = mff.marketLevel.bidAskQuote.add()
    q2.bidP, q2.bidQ = ltp - 1.0, 200
    q2.askP, q2.askQ = ltp + 1.0, 220

    ohlc = mff.marketOHLC.ohlc.add()
    ohlc.interval = "1d"
    ohlc.open, ohlc.high, ohlc.low, ohlc.close = ltp - 5, ltp + 2, ltp - 6, ltp
    ohlc.vol = 123456
    ohlc.ts = 1700000000000
    return resp.SerializeToString()


def _make_multi_instrument_response() -> bytes:
    """Build a FeedResponse with feeds for two instruments at once."""
    resp = pb.FeedResponse()
    resp.type = pb.Type.live_feed
    f1 = resp.feeds["NSE_INDEX:Nifty 50"]
    f1.ltpc.ltp = 24000.0
    f2 = resp.feeds["NSE_INDEX:Nifty Bank"]
    f2.ltpc.ltp = 52000.0
    return resp.SerializeToString()


# ── Protobuf decoding: LTPC ───────────────────────────────────────────

def test_decode_ltpc():
    raw = _make_ltpc_response("NSE_INDEX:Nifty 50", ltp=24523.45, cp=24400.0)
    ticks = _parse_feed_response(raw)

    assert len(ticks) == 1
    t = ticks[0]
    assert t.instrument_key == "NSE_INDEX|Nifty 50"   # ":" normalised to "|"
    assert t.mode == "ltpc"
    assert t.ltp == pytest.approx(24523.45)
    assert t.close_price == pytest.approx(24400.0)
    assert t.ltq == 50


def test_key_separator_normalised():
    raw = _make_ltpc_response("NSE_EQ:INE002A01018", ltp=1400.0)
    ticks = _parse_feed_response(raw)
    assert ticks[0].instrument_key == "NSE_EQ|INE002A01018"


# ── Protobuf decoding: IndexFullFeed ──────────────────────────────────

def test_decode_index_full_feed():
    raw = _make_index_full_response("NSE_INDEX:Nifty 50", ltp=24000.0)
    ticks = _parse_feed_response(raw)

    assert len(ticks) == 1
    t = ticks[0]
    assert t.ltp == pytest.approx(24000.0)
    assert len(t.ohlc) == 1
    assert t.ohlc[0].interval == "1d"
    assert t.ohlc[0].open == pytest.approx(23980.0)
    assert t.day_ohlc is not None
    assert t.day_ohlc.close == pytest.approx(24000.0)
    # Indices have no depth
    assert t.bids == []
    assert t.asks == []
    assert t.spread == 0.0


# ── Protobuf decoding: MarketFullFeed ─────────────────────────────────

def test_decode_market_full_feed():
    raw = _make_market_full_response("NSE_EQ:INE002A01018", ltp=1400.0)
    ticks = _parse_feed_response(raw)

    assert len(ticks) == 1
    t = ticks[0]
    assert t.ltp == pytest.approx(1400.0)
    assert t.volume == 123456
    assert t.oi == pytest.approx(7890.0)
    assert t.atp == pytest.approx(1399.5)
    assert t.iv == pytest.approx(0.18)
    assert t.total_buy_qty == pytest.approx(1000.0)
    assert t.total_sell_qty == pytest.approx(800.0)

    # Depth
    assert len(t.bids) == 2
    assert len(t.asks) == 2
    assert t.bids[0].price == pytest.approx(1399.5)
    assert t.bids[0].quantity == 100
    assert t.asks[0].price == pytest.approx(1400.5)

    # Spread = best ask - best bid = 1400.5 - 1399.5 = 1.0
    assert t.spread == pytest.approx(1.0)


def test_depth_filters_zero_prices():
    """Quotes with zero price should be excluded from bids/asks."""
    resp = pb.FeedResponse()
    feed = resp.feeds["NSE_EQ:TEST"]
    mff = feed.fullFeed.marketFF
    mff.ltpc.ltp = 100.0
    # One valid, one empty (zero) quote
    q1 = mff.marketLevel.bidAskQuote.add()
    q1.bidP, q1.bidQ, q1.askP, q1.askQ = 99.0, 10, 101.0, 10
    q2 = mff.marketLevel.bidAskQuote.add()
    q2.bidP, q2.bidQ, q2.askP, q2.askQ = 0.0, 0, 0.0, 0
    raw = resp.SerializeToString()

    ticks = _parse_feed_response(raw)
    t = ticks[0]
    assert len(t.bids) == 1   # zero-price level excluded
    assert len(t.asks) == 1


# ── Protobuf decoding: multi-instrument frame ─────────────────────────

def test_decode_multi_instrument():
    raw = _make_multi_instrument_response()
    ticks = _parse_feed_response(raw)

    assert len(ticks) == 2
    by_key = {t.instrument_key: t.ltp for t in ticks}
    assert by_key["NSE_INDEX|Nifty 50"] == pytest.approx(24000.0)
    assert by_key["NSE_INDEX|Nifty Bank"] == pytest.approx(52000.0)


def test_decode_empty_frame():
    """An empty FeedResponse should decode to no ticks, not raise."""
    resp = pb.FeedResponse()
    raw = resp.SerializeToString()
    ticks = _parse_feed_response(raw)
    assert ticks == []


# ── Subscription message construction ─────────────────────────────────

def test_build_subscription_message():
    msg = _build_subscription_message(["NSE_INDEX|Nifty 50"], "ltpc")
    parsed = json.loads(msg)
    assert parsed["method"] == "sub"
    assert parsed["data"]["mode"] == "ltpc"
    assert parsed["data"]["instrumentKeys"] == ["NSE_INDEX|Nifty 50"]
    assert "guid" in parsed and len(parsed["guid"]) > 0


def test_build_unsubscribe_message():
    msg = _build_unsubscribe_message(["NSE_INDEX|Nifty 50"])
    parsed = json.loads(msg)
    assert parsed["method"] == "unsub"
    assert parsed["data"]["instrumentKeys"] == ["NSE_INDEX|Nifty 50"]


def test_subscription_guids_unique():
    m1 = json.loads(_build_subscription_message(["X"], "ltpc"))
    m2 = json.loads(_build_subscription_message(["X"], "ltpc"))
    assert m1["guid"] != m2["guid"]


# ── LiveStream validation ─────────────────────────────────────────────

def test_livestream_empty_instruments_raises():
    with pytest.raises(ValueError, match="must not be empty"):
        LiveStream(instrument_keys=[], mode="ltpc")


def test_livestream_invalid_mode_raises():
    with pytest.raises(ValueError, match="mode must be one of"):
        LiveStream(instrument_keys=["NSE_INDEX|Nifty 50"], mode="bogus")


def test_livestream_too_many_instruments_raises():
    keys = [f"NSE_EQ|INST{i}" for i in range(101)]
    with pytest.raises(ValueError, match="Maximum"):
        LiveStream(instrument_keys=keys, mode="ltpc")


def test_livestream_valid_modes():
    for mode in ("ltpc", "full_d5", "full_d30", "option_greeks"):
        s = LiveStream(instrument_keys=["NSE_INDEX|Nifty 50"], mode=mode)
        assert s.mode == mode


def test_livestream_stores_config():
    s = LiveStream(
        instrument_keys=["NSE_INDEX|Nifty 50", "NSE_INDEX|Nifty Bank"],
        mode="full_d5",
        auto_reconnect=False,
        max_reconnects=3,
    )
    assert len(s.instrument_keys) == 2
    assert s.auto_reconnect is False
    assert s.max_reconnects == 3


# ── Tick dataclass helpers ────────────────────────────────────────────

def test_tick_spread_with_depth():
    t = Tick(
        instrument_key="X", mode="full",
        bids=[Depth(price=99.0, quantity=10)],
        asks=[Depth(price=101.0, quantity=10)],
    )
    assert t.spread == pytest.approx(2.0)


def test_tick_spread_no_depth_is_zero():
    t = Tick(instrument_key="X", mode="ltpc")
    assert t.spread == 0.0


def test_tick_day_ohlc_selection():
    t = Tick(
        instrument_key="X", mode="full",
        ohlc=[
            OHLC(interval="1m", open=1, high=2, low=0, close=1, volume=0, ts=0),
            OHLC(interval="1d", open=10, high=20, low=5, close=15, volume=0, ts=0),
        ],
    )
    day = t.day_ohlc
    assert day is not None
    assert day.interval == "1d"
    assert day.close == 15
