"""Strategy interface for producing close-of-candle signals."""
from abc import ABC, abstractmethod
import pandas as pd


class Strategy(ABC):
    @abstractmethod
    def signals(self, candles: pd.DataFrame) -> pd.DataFrame:
        """Return a frame with indicators, entry and exit columns."""
