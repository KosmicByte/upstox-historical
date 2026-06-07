"""
stream.py — Live market data feed via Upstox WebSocket V3.

Uses the pure ``websockets`` library + Protobuf decoding.
No SDK required.

Flow
----
1. GET /v3/feed/market-data-feed/authorize  → one-time wss:// URL
2. Connect to wss:// URL
3. Send subscription JSON  → {"guid": "...", "method": "sub", "data": {...}}
4. Receive binary Protobuf frames  → decode with FeedResponse proto
5. Parse into Tick dataclass  → deliver to your callback

Supported modes (RequestMode from proto)
-----------------------------------------
  "ltpc"          — LTP + close price only  (lightest, default)
  "full_d5"       — Full feed + 5-level market depth
  "full_d30"      — Full feed + 30-level market depth
  "option_greeks" — LTPC + greeks + first-level depth

Usage::

    import asyncio
    from upstox_historical.stream import LiveStream, Tick

    def on_tick(tick: Tick) -> None:
        print(tick.instrument_key, tick.ltp)

    async def main():
        stream = LiveStream(
            instrument_keys=["NSE_INDEX|Nifty 50", "NSE_INDEX|Nifty Bank"],
            mode="ltpc",
            on_tick=on_tick,
        )
        await stream.connect()

    asyncio.run(main())
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

import websockets
from websockets.exceptions import ConnectionClosed

from upstox_historical.market_info import get_ws_authorize_url

# Import the generated protobuf module
# Generated from: MarketDataFeed.proto (Upstox v3)
try:
    from upstox_historical.proto import MarketDataFeed_pb2 as pb  # type: ignore
except ImportError as e:
    raise ImportError(
        "Protobuf module not found. Run the generation step:\n"
        "  cd src/upstox_historical/proto\n"
        "  python -m grpc_tools.protoc -I. --python_out=. MarketDataFeed.proto\n"
        "Or use the pre-generated MarketDataFeed_pb2.py committed in the repo."
    ) from e

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────

_VALID_MODES = {"ltpc", "full_d5", "full_d30", "option_greeks"}
_MAX_INSTRUMENTS = 100   # Upstox recommended per connection
_RECONNECT_DELAY  = 3.0  # seconds before reconnect attempt
_MAX_RECONNECTS   = 10   # give up after this many consecutive failures
_PING_INTERVAL    = 20   # seconds between WebSocket pings


# ── Data types ────────────────────────────────────────────────────────

@dataclass
class OHLC:
    """Single OHLC candle from the live feed."""
    interval: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    ts: int             # unix timestamp of candle


@dataclass
class Depth:
    """Single bid or ask level."""
    price: float
    quantity: int


@dataclass
class Tick:
    """
    Normalised tick from the live WebSocket feed.

    All fields are populated based on the subscription mode:
      - ``ltpc``          → ltp, ltt, ltq, close_price only
      - ``full_d5/d30``   → all fields including depth and ohlc
      - ``option_greeks`` → ltp + greeks + first_depth

    Fields that are not available in the current mode are left at
    their zero/empty defaults.
    """
    instrument_key: str         # e.g. "NSE_INDEX|Nifty 50"
    mode: str                   # subscription mode

    # LTPC
    ltp: float   = 0.0          # last traded price
    ltt: int     = 0            # last trade timestamp (epoch ms)
    ltq: int     = 0            # last trade quantity
    close_price: float = 0.0    # previous close

    # Volume / OI
    volume: int  = 0            # volume traded today
    oi: float    = 0.0          # open interest
    atp: float   = 0.0          # average traded price
    total_buy_qty:  float = 0.0
    total_sell_qty: float = 0.0

    # Options
    iv: float    = 0.0          # implied volatility
    delta: float = 0.0
    theta: float = 0.0
    gamma: float = 0.0
    vega:  float = 0.0
    rho:   float = 0.0

    # Market depth (up to 5 or 30 levels)
    bids: list[Depth] = field(default_factory=list)
    asks: list[Depth] = field(default_factory=list)

    # OHLC candles (multiple intervals may be present)
    ohlc: list[OHLC] = field(default_factory=list)

    @property
    def spread(self) -> float:
        """Best ask − best bid. Returns 0.0 if depth not available."""
        if self.bids and self.asks:
            return round(self.asks[0].price - self.bids[0].price, 4)
        return 0.0

    @property
    def day_ohlc(self) -> OHLC | None:
        """Today's 1D candle, if present in feed."""
        for o in self.ohlc:
            if o.interval in ("1d", "day", "I1D"):
                return o
        return self.ohlc[0] if self.ohlc else None


# ── Protobuf parser ───────────────────────────────────────────────────

def _parse_feed_response(raw_bytes: bytes) -> list[Tick]:
    """
    Decode a raw Protobuf binary frame from Upstox into a list of Ticks.

    One frame can contain feeds for multiple instruments.
    """
    response = pb.FeedResponse()
    response.ParseFromString(raw_bytes)

    ticks: list[Tick] = []

    for instrument_key, feed in response.feeds.items():
        # Normalise key separator (response uses ":" not "|")
        norm_key = instrument_key.replace(":", "|")

        # Determine which union field is set
        union = feed.WhichOneof("FeedUnion")

        if union == "ltpc":
            ltpc = feed.ltpc
            ticks.append(Tick(
                instrument_key=norm_key,
                mode="ltpc",
                ltp=ltpc.ltp,
                ltt=ltpc.ltt,
                ltq=ltpc.ltq,
                close_price=ltpc.cp,
            ))

        elif union == "fullFeed":
            ff = feed.fullFeed
            inner = ff.WhichOneof("FullFeedUnion")

            if inner == "marketFF":
                mff = ff.marketFF
                ltpc = mff.ltpc

                bids = [
                    Depth(price=q.bidP, quantity=q.bidQ)
                    for q in mff.marketLevel.bidAskQuote
                    if q.bidP > 0
                ]
                asks = [
                    Depth(price=q.askP, quantity=q.askQ)
                    for q in mff.marketLevel.bidAskQuote
                    if q.askP > 0
                ]
                ohlc_list = [
                    OHLC(
                        interval=o.interval,
                        open=o.open, high=o.high,
                        low=o.low,  close=o.close,
                        volume=o.vol, ts=o.ts,
                    )
                    for o in mff.marketOHLC.ohlc
                ]
                ticks.append(Tick(
                    instrument_key=norm_key,
                    mode="full",
                    ltp=ltpc.ltp,
                    ltt=ltpc.ltt,
                    ltq=ltpc.ltq,
                    close_price=ltpc.cp,
                    volume=mff.vtt,
                    oi=mff.oi,
                    atp=mff.atp,
                    iv=mff.iv,
                    total_buy_qty=mff.tbq,
                    total_sell_qty=mff.tsq,
                    bids=bids,
                    asks=asks,
                    ohlc=ohlc_list,
                ))

            elif inner == "indexFF":
                iff = ff.indexFF
                ltpc = iff.ltpc
                ohlc_list = [
                    OHLC(
                        interval=o.interval,
                        open=o.open, high=o.high,
                        low=o.low,  close=o.close,
                        volume=o.vol, ts=o.ts,
                    )
                    for o in iff.marketOHLC.ohlc
                ]
                ticks.append(Tick(
                    instrument_key=norm_key,
                    mode="full",
                    ltp=ltpc.ltp,
                    ltt=ltpc.ltt,
                    ltq=ltpc.ltq,
                    close_price=ltpc.cp,
                    ohlc=ohlc_list,
                ))

        elif union == "firstLevelWithGreeks":
            flg = feed.firstLevelWithGreeks
            ltpc = flg.ltpc
            g = flg.optionGreeks
            fd = flg.firstDepth
            ticks.append(Tick(
                instrument_key=norm_key,
                mode="option_greeks",
                ltp=ltpc.ltp,
                ltt=ltpc.ltt,
                ltq=ltpc.ltq,
                close_price=ltpc.cp,
                volume=flg.vtt,
                oi=flg.oi,
                iv=flg.iv,
                delta=g.delta,
                theta=g.theta,
                gamma=g.gamma,
                vega=g.vega,
                rho=g.rho,
                bids=[Depth(price=fd.bidP, quantity=fd.bidQ)] if fd.bidP else [],
                asks=[Depth(price=fd.askP, quantity=fd.askQ)] if fd.askP else [],
            ))

    return ticks


def _build_subscription_message(
    instrument_keys: list[str],
    mode: str,
) -> str:
    """Build the JSON subscription message to send after connecting."""
    return json.dumps({
        "guid": str(uuid.uuid4()),
        "method": "sub",
        "data": {
            "mode": mode,
            "instrumentKeys": instrument_keys,
        },
    })


def _build_unsubscribe_message(instrument_keys: list[str]) -> str:
    return json.dumps({
        "guid": str(uuid.uuid4()),
        "method": "unsub",
        "data": {"instrumentKeys": instrument_keys},
    })


# ── LiveStream ────────────────────────────────────────────────────────

# Type aliases
OnTickCallback = Callable[[Tick], None]
OnTickAsync    = Callable[[Tick], Coroutine[Any, Any, None]]
OnErrorCallback = Callable[[Exception], None]
OnStatusCallback = Callable[[str], None]   # "connected" | "disconnected" | "reconnecting"


class LiveStream:
    """
    Async WebSocket client for the Upstox Market Data Feed V3.

    Connects, subscribes, decodes Protobuf ticks, and delivers them
    to your callback. Handles reconnects automatically.

    Parameters
    ----------
    instrument_keys : list[str]
        NSE instrument keys to subscribe to.
        e.g. ``["NSE_INDEX|Nifty 50", "NSE_EQ|INE002A01018"]``
    mode : str
        Subscription mode: ``"ltpc"`` | ``"full_d5"`` | ``"full_d30"`` | ``"option_greeks"``
    on_tick : callable
        Called for every decoded Tick. Can be sync or async.
    on_error : callable, optional
        Called when an exception occurs. Default: logs the error.
    on_status : callable, optional
        Called with a status string on connect / disconnect / reconnect.
    token : str, optional
        Override token. Defaults to UPSTOX_ANALYTICS_TOKEN from .env.
    auto_reconnect : bool
        Whether to reconnect on disconnect. Default: True.
    max_reconnects : int
        Maximum consecutive reconnect attempts before giving up.

    Example
    -------
    ::

        async def main():
            stream = LiveStream(
                instrument_keys=["NSE_INDEX|Nifty 50"],
                mode="ltpc",
                on_tick=lambda t: print(t.instrument_key, t.ltp),
            )
            await stream.connect()   # runs until stopped or market closes

        asyncio.run(main())
    """

    def __init__(
        self,
        instrument_keys: list[str],
        mode: str = "ltpc",
        on_tick: OnTickCallback | OnTickAsync | None = None,
        on_error: OnErrorCallback | None = None,
        on_status: OnStatusCallback | None = None,
        token: str | None = None,
        auto_reconnect: bool = True,
        max_reconnects: int = _MAX_RECONNECTS,
    ) -> None:
        if not instrument_keys:
            raise ValueError("instrument_keys must not be empty.")
        if mode not in _VALID_MODES:
            raise ValueError(f"mode must be one of {_VALID_MODES}. Got: {mode!r}")
        if len(instrument_keys) > _MAX_INSTRUMENTS:
            raise ValueError(
                f"Maximum {_MAX_INSTRUMENTS} instruments per connection. "
                f"Got {len(instrument_keys)}. Split into multiple LiveStream instances."
            )

        self.instrument_keys = instrument_keys
        self.mode = mode
        self.token = token
        self.auto_reconnect = auto_reconnect
        self.max_reconnects = max_reconnects

        self._on_tick = on_tick
        self._on_error = on_error or self._default_on_error
        self._on_status = on_status or self._default_on_status

        self._ws: Any = None
        self._running = False
        self._reconnect_count = 0

    # ── Lifecycle ─────────────────────────────────────────────────────

    async def connect(self) -> None:
        """
        Connect to the WebSocket and start receiving ticks.

        Blocks until stopped via ``stop()`` or until ``max_reconnects``
        is exceeded. Call from an asyncio event loop.
        """
        self._running = True
        self._reconnect_count = 0

        while self._running:
            try:
                await self._run_session()
                # Clean exit — stop reconnecting
                break

            except ConnectionClosed as exc:
                logger.warning("WebSocket closed: %s", exc)
                if not self._running:
                    break
                await self._maybe_reconnect()

            except Exception as exc:
                self._on_error(exc)
                if not self._running:
                    break
                await self._maybe_reconnect()

        logger.info("LiveStream stopped.")

    async def stop(self) -> None:
        """Gracefully disconnect and stop the stream."""
        logger.info("LiveStream.stop() called — closing WebSocket.")
        self._running = False
        if self._ws is not None:
            await self._ws.close()

    # ── Internal session ──────────────────────────────────────────────

    async def _run_session(self) -> None:
        """Run a single WebSocket session (connect → subscribe → receive)."""

        # Step 1: get a fresh one-time wss:// URL
        logger.info("Fetching WebSocket authorize URL...")
        wss_url = get_ws_authorize_url(token=self.token)
        logger.info("Connecting to feed: %s...", wss_url[:60])

        async with websockets.connect(
            wss_url,
            ping_interval=_PING_INTERVAL,
            ping_timeout=10,
            close_timeout=5,
        ) as ws:
            self._ws = ws
            self._reconnect_count = 0   # reset on successful connect
            self._on_status("connected")
            logger.info(
                "Connected. Subscribing %d instruments in mode=%s",
                len(self.instrument_keys), self.mode,
            )

            # Step 2: send subscription
            sub_msg = _build_subscription_message(self.instrument_keys, self.mode)
            await ws.send(sub_msg)
            logger.debug("Subscription sent: %s", sub_msg)

            # Step 3: receive loop
            async for message in ws:
                if not self._running:
                    break

                if isinstance(message, bytes):
                    await self._handle_binary(message)
                else:
                    # Upstox may send text frames for status/errors
                    logger.debug("Text frame: %s", message)

        self._ws = None
        self._on_status("disconnected")

    async def _handle_binary(self, raw: bytes) -> None:
        """Decode a Protobuf binary frame and dispatch ticks."""
        try:
            ticks = _parse_feed_response(raw)
        except Exception as exc:
            logger.warning("Failed to decode frame (%d bytes): %s", len(raw), exc)
            return

        for tick in ticks:
            try:
                if asyncio.iscoroutinefunction(self._on_tick):
                    await self._on_tick(tick)  # type: ignore[misc]
                elif self._on_tick is not None:
                    self._on_tick(tick)
            except Exception as exc:
                logger.error("on_tick callback raised: %s", exc)

    async def _maybe_reconnect(self) -> None:
        """Wait and decide whether to reconnect."""
        if not self.auto_reconnect:
            self._running = False
            return

        self._reconnect_count += 1
        if self._reconnect_count > self.max_reconnects:
            logger.error(
                "Max reconnects (%d) exceeded. Stopping.", self.max_reconnects
            )
            self._running = False
            return

        delay = _RECONNECT_DELAY * self._reconnect_count   # back-off
        logger.info(
            "Reconnecting in %.1fs (attempt %d/%d)...",
            delay, self._reconnect_count, self.max_reconnects,
        )
        self._on_status("reconnecting")
        await asyncio.sleep(delay)

    # ── Default callbacks ─────────────────────────────────────────────

    @staticmethod
    def _default_on_error(exc: Exception) -> None:
        logger.error("LiveStream error: %s", exc, exc_info=True)

    @staticmethod
    def _default_on_status(status: str) -> None:
        logger.info("LiveStream status: %s", status)

    # ── Subscription management (while connected) ─────────────────────

    async def subscribe(self, instrument_keys: list[str], mode: str | None = None) -> None:
        """
        Add instruments to an active stream (without reconnecting).

        Parameters
        ----------
        instrument_keys : list[str]
            New instruments to add.
        mode : str, optional
            Override mode for these instruments. Defaults to stream mode.
        """
        if self._ws is None:
            raise RuntimeError("Not connected. Call connect() first.")
        msg = _build_subscription_message(instrument_keys, mode or self.mode)
        await self._ws.send(msg)
        self.instrument_keys.extend(instrument_keys)
        logger.info("Subscribed %d more instruments.", len(instrument_keys))

    async def unsubscribe(self, instrument_keys: list[str]) -> None:
        """Remove instruments from an active stream."""
        if self._ws is None:
            raise RuntimeError("Not connected. Call connect() first.")
        msg = _build_unsubscribe_message(instrument_keys)
        await self._ws.send(msg)
        for k in instrument_keys:
            if k in self.instrument_keys:
                self.instrument_keys.remove(k)
        logger.info("Unsubscribed %d instruments.", len(instrument_keys))


# ── Convenience runner ────────────────────────────────────────────────

def run_stream(
    instrument_keys: list[str],
    mode: str = "ltpc",
    on_tick: OnTickCallback | None = None,
    on_error: OnErrorCallback | None = None,
    on_status: OnStatusCallback | None = None,
    token: str | None = None,
) -> None:
    """
    Blocking convenience wrapper — runs an event loop with LiveStream.

    Useful for scripts and CLI commands that don't already have a
    running event loop.

    Example
    -------
    ::

        from upstox_historical.stream import run_stream

        run_stream(
            instrument_keys=["NSE_INDEX|Nifty 50"],
            mode="ltpc",
            on_tick=lambda t: print(t.ltp),
        )
    """
    stream = LiveStream(
        instrument_keys=instrument_keys,
        mode=mode,
        on_tick=on_tick,
        on_error=on_error,
        on_status=on_status,
        token=token,
    )
    asyncio.run(stream.connect())
