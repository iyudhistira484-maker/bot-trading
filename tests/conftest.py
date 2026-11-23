import numpy as np
import pandas as pd
import pytest

from src.config import load_config


@pytest.fixture
def config():
    return load_config()


@pytest.fixture
def candles():
    close = 100 + np.sin(np.arange(320) / 7) * 5 + np.arange(320) / 20
    return pd.DataFrame({'timestamp': pd.date_range('2023-01-01', periods=len(close), freq='h', tz='UTC'),
                         'open': close, 'high': close + 1, 'low': close - 1,
                         'close': close, 'volume': 10.0})
