"""Public OHLCV retrieval, validation and CSV persistence."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

import ccxt
import numpy as np
import pandas as pd

COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]


def validate_candles(frame: pd.DataFrame, interval_ms: int | None = None) -> pd.DataFrame:
    """Return a sorted UTC frame; reject invalid, duplicated or missing bars."""
    if frame.empty:
        raise ValueError("OHLCV data is empty")
    if not set(COLUMNS).issubset(frame.columns):
        raise ValueError("Missing OHLCV columns")
    df = frame.loc[:, COLUMNS].copy()
    if pd.api.types.is_numeric_dtype(df.timestamp):
        df['timestamp'] = pd.to_datetime(df.timestamp, unit='ms', utc=True, errors='raise')
    else:
        df['timestamp'] = pd.to_datetime(df.timestamp, utc=True, errors='raise')
    if df.timestamp.isna().any():
        raise ValueError("Missing timestamp")
    for col in COLUMNS[1:]:
        df[col] = pd.to_numeric(df[col], errors='raise')
    if not np.isfinite(df[COLUMNS[1:]].to_numpy()).all():
        raise ValueError("Non-finite OHLCV")
    if (df[['open', 'high', 'low', 'close']] <= 0).any().any() or (df.volume < 0).any():
        raise ValueError("Invalid OHLCV values")
    if ((df.high < df[['open', 'close', 'low']].max(axis=1)) | (df.low > df[['open', 'close', 'high']].min(axis=1))).any():
        raise ValueError("Inconsistent OHLC prices")
    df = df.sort_values('timestamp').reset_index(drop=True)
    if df.timestamp.duplicated().any():
        raise ValueError("Duplicate candle timestamp")
    if interval_ms is not None:
        if interval_ms <= 0:
            raise ValueError("interval_ms must be positive")
        if (df.timestamp.astype('int64') // 1_000_000 % interval_ms != 0).any():
            raise ValueError("Candle timestamp not aligned to timeframe")
        if len(df) > 1 and (df.timestamp.diff().iloc[1:] != pd.Timedelta(milliseconds=interval_ms)).any():
            raise ValueError("Gap in candle data; obtain complete data before backtesting")
    return df


def closed_candles(frame: pd.DataFrame, interval_ms: int, now_ms: int) -> pd.DataFrame:
    """Exclude candles whose close time is later than now."""
    df = validate_candles(frame, interval_ms)
    return df[df.timestamp + pd.Timedelta(milliseconds=interval_ms) <= pd.to_datetime(now_ms, unit='ms', utc=True)].reset_index(drop=True)


def fetch_history(exchange: Any, symbol: str, timeframe: str, since_ms: int, until_ms: int,
                  limit: int = 500, retries: int = 3, delay: float = 1.0,
                  sleep: Callable[[float], None] = time.sleep) -> pd.DataFrame:
    """Fetch paginated public candles. Retry only idempotent public requests."""
    interval_ms = exchange.parse_timeframe(timeframe) * 1000
    if interval_ms <= 0 or since_ms >= until_ms or limit <= 0 or retries < 0 or delay < 0:
        raise ValueError("Invalid history request")
    cursor = since_ms
    rows: dict[int, list] = {}
    while cursor < until_ms:
        for attempt in range(retries + 1):
            try:
                page = exchange.fetch_ohlcv(symbol, timeframe, since=cursor, limit=limit)
                break
            except (ccxt.NetworkError, ccxt.RequestTimeout):
                if attempt == retries:
                    raise
                sleep(delay * 2 ** attempt)
        if not page:
            break
        for row in page:
            if len(row) != 6:
                raise ValueError("Malformed OHLCV row")
            timestamp = int(row[0])
            if since_ms <= timestamp < until_ms:
                if timestamp in rows and rows[timestamp] != list(row):
                    raise ValueError("Conflicting candles returned by exchange")
                rows[timestamp] = list(row)
        last = max(int(row[0]) for row in page)
        if last < cursor:
            raise ValueError("Exchange pagination did not advance")
        cursor = last + interval_ms
    df = pd.DataFrame(list(rows.values()), columns=COLUMNS)
    df = closed_candles(df, interval_ms, min(until_ms, exchange.milliseconds()))
    if df.empty:
        raise ValueError("No closed candles available")
    first_expected = ((since_ms + interval_ms - 1) // interval_ms) * interval_ms
    last_expected = (min(until_ms, exchange.milliseconds()) // interval_ms - 1) * interval_ms
    if int(df.timestamp.iloc[0].timestamp() * 1000) > first_expected or int(df.timestamp.iloc[-1].timestamp() * 1000) < last_expected:
        raise ValueError("Incomplete requested history; obtain a complete external CSV or shorten the period")
    return df


def save_csv(frame: pd.DataFrame, path: str | Path) -> None:
    df = validate_candles(frame)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


def load_csv(path: str | Path, interval_ms: int | None = None) -> pd.DataFrame:
    return validate_candles(pd.read_csv(path), interval_ms)
