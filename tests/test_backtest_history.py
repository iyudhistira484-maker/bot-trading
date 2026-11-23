"""Offline reproduction of Indodax history truncated from an unbounded range."""
import ccxt
import pytest

from src.backtest.history import fetch_indodax_history
from src.data.ohlcv import fetch_history


class TruncatingIndodax:
    id = 'indodax'
    def __init__(self, first, count=6, missing=None):
        self.first = first
        self.until = first + count * 3_600_000
        self.calls = []
        self.rows = [[first + i * 3_600_000, 100, 101, 99, 100, 1]
                     for i in range(count) if i != missing]
    def parse_timeframe(self, timeframe): return 3600
    def milliseconds(self): return self.until
    def fetch_ohlcv(self, symbol, timeframe, since, limit, params=None):
        self.calls.append((since, limit, params))
        # Fake server caps a too-large range to its 3 most recent candles.
        end_ms = params['until'] * 1000 if params else self.until
        rows = [r for r in self.rows if since <= r[0] <= end_ms]
        return sorted(rows[-3:])[:limit]


def test_bounded_windows_avoid_oldest_candles_being_truncated():
    first = 1_700_000_000_000 // 3_600_000 * 3_600_000
    ex = TruncatingIndodax(first)
    with pytest.raises(ValueError, match='Incomplete requested history'):
        fetch_history(ex, 'BTC/IDR', '1h', first, ex.until, limit=3)
    ex.calls.clear()
    df = fetch_indodax_history(ex, 'BTC/IDR', '1h', first, ex.until, limit=3)
    assert len(df) == 6
    assert int(df.timestamp.iloc[0].timestamp() * 1000) == first
    assert int(df.timestamp.iloc[-1].timestamp() * 1000) == ex.until - 3_600_000
    assert ex.calls == [(first, 3, {'until': (first + 3 * 3_600_000 - 1) // 1000}),
                        (first + 3 * 3_600_000, 3, {'until': (ex.until - 1) // 1000})]


def test_bounded_windows_fail_closed_on_gaps():
    first = 1_700_000_000_000 // 3_600_000 * 3_600_000
    ex = TruncatingIndodax(first, missing=1)
    with pytest.raises(ValueError, match='Gap|Incomplete'):
        fetch_indodax_history(ex, 'BTC/IDR', '1h', first, ex.until, limit=3)


def test_rejects_different_exchange():
    ex = TruncatingIndodax(1_700_000_000_000)
    ex.id = 'not-indodax'
    with pytest.raises(ValueError, match='only implemented for Indodax'):
        fetch_indodax_history(ex, 'BTC/IDR', '1h', ex.first, ex.until)
    assert not ex.calls


def test_actual_ccxt_indodax_adapter_uses_bounded_second_windows(monkeypatch):
    first = 1_700_000_000_000 // 3_600_000 * 3_600_000
    end = first + 6 * 3_600_000
    ex = ccxt.indodax({'enableRateLimit': True})
    ex.set_markets([{'id': 'btc_idr', 'symbol': 'BTC/IDR', 'base': 'BTC',
                     'quote': 'IDR', 'spot': True, 'type': 'spot'}])
    monkeypatch.setattr(ex, 'milliseconds', lambda: end)
    requests = []
    def response(params):
        requests.append(params.copy())
        rows = [{'Time': (first + i * 3_600_000) // 1000, 'Open': 100, 'High': 101,
                 'Low': 99, 'Close': 100, 'Volume': '1'} for i in range(6)]
        rows = [r for r in rows if params['from'] <= r['Time'] <= params['to']]
        return rows[-3:]
    monkeypatch.setattr(ex, 'publicGetTradingviewHistoryV2', response)
    frame = fetch_indodax_history(ex, 'BTC/IDR', '1h', first, end, limit=3)
    assert len(frame) == 6
    assert [(r['from'], r['to']) for r in requests] == [
        (first // 1000, (first + 3 * 3_600_000 - 1) // 1000),
        ((first + 3 * 3_600_000) // 1000, (end - 1) // 1000),
    ]
