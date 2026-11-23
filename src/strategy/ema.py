"""EMA crossover strategy: signals refer only to the current and past bars."""
import numpy as np
import pandas as pd
from ta.momentum import RSIIndicator
from ta.volatility import AverageTrueRange

from src.config import StrategyConfig
from src.data.ohlcv import validate_candles
from src.strategy.base import Strategy


class EMATrendStrategy(Strategy):
    def __init__(self, config: StrategyConfig):
        self.config = config

    def signals(self, candles: pd.DataFrame) -> pd.DataFrame:
        df = validate_candles(candles)
        c = self.config
        for name, length in [('ema_fast', c.ema_fast), ('ema_slow', c.ema_slow), ('ema_trend', c.ema_trend)]:
            df[name] = df.close.ewm(span=length, adjust=False, min_periods=length).mean()
        df['rsi'] = RSIIndicator(df.close, window=c.rsi_period).rsi()
        # ta's ATR requires at least window rows and initializes early values to zero.
        df['atr'] = np.nan
        if len(df) >= c.atr_period:
            atr = AverageTrueRange(df.high, df.low, df.close, window=c.atr_period).average_true_range()
            atr.iloc[:c.atr_period - 1] = np.nan
            df['atr'] = atr
        upward = (df.ema_fast > df.ema_slow) & (df.ema_fast.shift(1) <= df.ema_slow.shift(1))
        downward = (df.ema_fast < df.ema_slow) & (df.ema_fast.shift(1) >= df.ema_slow.shift(1))
        df['entry'] = (upward & (df.close > df.ema_trend) & df.rsi.between(c.rsi_min, c.rsi_max) & (df.atr > c.atr_min)).fillna(False)
        df['exit'] = downward.fillna(False)
        return df
