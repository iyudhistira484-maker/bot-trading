"""Indodax-specific bounded public history requests for the backtest only.

ccxt 4.5.93's Indodax adapter accepts ``params['until']`` in *seconds* and
otherwise requests all the way to the current time. The API may truncate a
large range to recent candles, so limit each window. This wrapper does not
change the paper bot's market data requests.
"""
from __future__ import annotations

from src.data.ohlcv import fetch_history


class _BoundedIndodax:
    def __init__(self, exchange, until_ms: int):
        if getattr(exchange, 'id', None) != 'indodax':
            raise ValueError('Bounded history is only implemented for Indodax')
        self.exchange = exchange
        self.until_ms = until_ms

    def parse_timeframe(self, timeframe):
        return self.exchange.parse_timeframe(timeframe)

    def milliseconds(self):
        return self.exchange.milliseconds()

    def fetch_ohlcv(self, symbol, timeframe, since, limit):
        interval_ms = self.parse_timeframe(timeframe) * 1000
        end_ms = min(self.until_ms, since + interval_ms * limit)
        # Use an exclusive upper boundary even if the API treats 'to' as
        # inclusive, so it cannot displace the first candle of a full page.
        # Indodax ccxt 4.5.93 passes until directly as seconds, unlike since.
        return self.exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=limit,
                                         params={'until': (end_ms - 1) // 1000})


def fetch_indodax_history(exchange, symbol, timeframe, since_ms, until_ms,
                           limit=500, retries=3, delay=1.0):
    """Fetch bounded windows, still rejecting missing/gapped/incomplete history."""
    bounded = _BoundedIndodax(exchange, until_ms)
    return fetch_history(bounded, symbol, timeframe, since_ms, until_ms,
                         limit=limit, retries=retries, delay=delay)
