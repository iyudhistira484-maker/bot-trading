import numpy as np
import pytest

from src.strategy.ema import EMATrendStrategy


def test_indicators_and_warmup(candles, config):
    s = EMATrendStrategy(config.strategy).signals(candles)
    assert s.ema_fast.iloc[19] == pytest.approx(candles.close.ewm(span=20, adjust=False).mean().iloc[19])
    assert np.isnan(s.ema_trend.iloc[198])
    assert not s.entry.iloc[:199].any()
    assert s.atr.iloc[13] > 0
    assert s.rsi.dropna().between(0, 100).all()


def test_signals_use_no_future(candles, config):
    strategy = EMATrendStrategy(config.strategy)
    whole = strategy.signals(candles)
    truncated = strategy.signals(candles.iloc[:250])
    for col in ['ema_fast', 'ema_slow', 'ema_trend', 'rsi', 'atr', 'entry', 'exit']:
        np.testing.assert_allclose(whole[col].iloc[:250].to_numpy(), truncated[col].to_numpy(), equal_nan=True)
    for i in np.flatnonzero(whole.entry):
        r, previous = whole.iloc[i], whole.iloc[i-1]
        assert r.ema_fast > r.ema_slow and previous.ema_fast <= previous.ema_slow
        assert r.close > r.ema_trend and config.strategy.rsi_min <= r.rsi <= config.strategy.rsi_max
        assert r.atr > config.strategy.atr_min


def test_short_history_no_atr_exception(candles, config):
    s = EMATrendStrategy(config.strategy).signals(candles.iloc[:3])
    assert s.atr.isna().all() and not s.entry.any()


def test_real_cross_entry_and_filters(config):
    import pandas as pd
    close = [10., 9., 8., 7., 8., 9., 10., 11.]
    candles = pd.DataFrame({'timestamp': pd.date_range('2023-01-01', periods=len(close), freq='h', tz='UTC'),
                            'open': close, 'high': [x+1 for x in close], 'low': [x-1 for x in close],
                            'close': close, 'volume': 1.})
    cfg = config.strategy.model_copy(update={'ema_fast': 2, 'ema_slow': 3, 'ema_trend': 4,
                                             'rsi_period': 2, 'rsi_min': 0, 'rsi_max': 100, 'atr_period': 2})
    assert EMATrendStrategy(cfg).signals(candles).entry.sum() == 1
    cfg = cfg.model_copy(update={'atr_min': 100.})
    assert not EMATrendStrategy(cfg).signals(candles).entry.any()
