import ccxt
import pandas as pd
import pytest

from src.data.ohlcv import validate_candles, closed_candles, fetch_history, load_csv, save_csv


def test_empty_invalid_and_gaps(candles):
    with pytest.raises(ValueError): validate_candles(candles.iloc[:0])
    with pytest.raises(ValueError): validate_candles(pd.concat([candles, candles.iloc[:1]]), 3600000)
    with pytest.raises(ValueError, match='Gap'): validate_candles(candles.drop(index=5), 3600000)
    broken = candles.copy(); broken.loc[0, 'high'] = 0
    with pytest.raises(ValueError): validate_candles(broken)


def test_closed_and_csv(candles, tmp_path):
    now = int((candles.timestamp.iloc[2] + pd.Timedelta(minutes=30)).timestamp() * 1000)
    result = closed_candles(candles, 3600000, now)
    assert len(result) == 2
    save_csv(result, tmp_path / 'candles.csv')
    pd.testing.assert_frame_equal(result, load_csv(tmp_path / 'candles.csv', 3600000))


def test_public_timeout_retry_and_pagination(candles):
    first = int(candles.timestamp.iloc[0].timestamp() * 1000)
    rows = [[int(r.timestamp.timestamp() * 1000), r.open, r.high, r.low, r.close, r.volume] for r in candles.iloc[:3].itertuples()]
    class Exchange:
        calls = 0
        def parse_timeframe(self, _): return 3600
        def milliseconds(self): return first + 3 * 3600000
        def fetch_ohlcv(self, symbol, timeframe, since, limit):
            self.calls += 1
            if self.calls == 1: raise ccxt.RequestTimeout('timeout')
            return [r for r in rows if r[0] >= since][:limit]
    ex = Exchange(); delays = []
    data = fetch_history(ex, 'BTC/IDR', '1h', first, first + 3 * 3600000, limit=2, sleep=delays.append)
    assert len(data) == 3 and delays == [1.0]
    assert ex.calls == 3


def test_public_timeout_exhausted(candles):
    class Exchange:
        def parse_timeframe(self, _): return 3600
        def fetch_ohlcv(self, *args, **kwargs): raise ccxt.RequestTimeout('timeout')
    with pytest.raises(ccxt.RequestTimeout):
        fetch_history(Exchange(), 'BTC/IDR', '1h', 0, 3600000, retries=1, sleep=lambda _: None)
