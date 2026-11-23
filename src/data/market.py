"""Fresh public OHLCV/ticker snapshots for the paper loop."""
import time
from typing import Any

import ccxt

from src.config import Config
from src.data.ohlcv import fetch_history
from src.execution.paper import Quote


class MarketData:
    def __init__(self, exchange: Any, config: Config):
        self.exchange, self.config = exchange, config
        self.interval_ms = exchange.parse_timeframe(config.market.timeframe) * 1000
        minimum = max(config.strategy.ema_trend + 2, config.strategy.rsi_period + 2, config.strategy.atr_period + 2)
        if not self.interval_ms or config.market.ohlcv_limit < minimum:
            raise ValueError('Invalid timeframe or OHLCV limit too small for indicator warmup')

    def _public(self, fn, *args):
        for attempt in range(self.config.market.retries + 1):
            try:
                return fn(*args)
            except ccxt.NetworkError:
                if attempt == self.config.market.retries:
                    raise
                time.sleep(self.config.market.retry_delay_seconds * 2 ** attempt)

    def load_markets(self):
        return self._public(self.exchange.load_markets)

    def snapshot(self, now_ms: int):
        quotes, frames = {}, {}
        last_close = (now_ms // self.interval_ms) * self.interval_ms
        # Include the full configured history, without using the still-open candle.
        since = last_close - self.config.market.ohlcv_limit * self.interval_ms
        for symbol in self.config.market.pairs:
            df = fetch_history(self.exchange, symbol, self.config.market.timeframe, since, now_ms,
                               self.config.market.ohlcv_limit, self.config.market.retries,
                               self.config.market.retry_delay_seconds)
            ticker = self._public(self.exchange.fetch_ticker, symbol)
            if any(ticker.get(key) is None for key in ('bid', 'ask', 'last', 'timestamp')):
                raise ValueError('Ticker missing bid, ask, last, or timestamp')
            last = df.iloc[-1]
            close_ms = int(last.timestamp.timestamp() * 1000) + self.interval_ms
            quotes[symbol] = Quote(float(ticker['bid']), float(ticker['ask']), float(ticker['last']),
                                   int(ticker['timestamp']), close_ms, float(last['close']))
            frames[symbol] = df
        return quotes, frames
