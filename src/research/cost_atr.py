"""Exploratory in-sample-only cost/stop-distance filter; never used by paper/live.

The 0.50 cutoff is an arbitrary hypothesis, not a recommendation. The input
history has already been observed, so this is not a blind validation. This
module never changes configuration, databases, or bot execution.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import ccxt
import numpy as np
import pandas as pd

from src.backtest.engine import Result, simulate
from src.backtest.metrics import calculate_metrics
from src.config import Config, load_config
from src.data.ohlcv import load_csv, validate_candles
from src.strategy.ema import EMATrendStrategy

# Fixed for this exploratory experiment; it is not tuned or a safety limit.
MAX_COST_TO_STOP = 0.50


@dataclass
class Experiment:
    baseline: Result
    filtered: Result
    eligible_signals: int
    blocked_signals: int
    in_sample_candles: int
    total_candles: int


def apply_cost_filter(signals: pd.DataFrame, config: Config) -> pd.DataFrame:
    """Return a copy with only entry signals filtered; never use the next open.

    The estimated round-trip cost per unit assumes both sides at the signal
    candle close, applies configured taker fee/tax and slippage on each side,
    and ignores spread. Divide it by the strategy's ATR stop distance. This is
    a rough price-scale estimate, not an executable quote or guaranteed loss.
    Missing/non-finite or non-positive inputs block entry, never exit.
    """
    result = signals.copy()
    close = pd.to_numeric(result['close'], errors='coerce')
    atr = pd.to_numeric(result['atr'], errors='coerce')
    execution = config.execution
    entry_ref = close * (1 + execution.slippage_rate)
    exit_ref = close * (1 - execution.slippage_rate)
    cost = (entry_ref * (1 + execution.taker_buy_fee_rate + execution.buy_tax_rate)
            - exit_ref * (1 - execution.taker_sell_fee_rate - execution.sell_tax_rate))
    distance = atr * config.strategy.atr_stop_multiplier
    with np.errstate(divide='ignore', invalid='ignore', over='ignore'):
        ratio = cost / distance
    valid = (np.isfinite(close) & (close > 0) & np.isfinite(atr) & (atr > 0)
             & np.isfinite(cost) & (cost >= 0) & np.isfinite(distance) & (distance > 0)
             & np.isfinite(ratio) & (ratio <= MAX_COST_TO_STOP))
    result['entry'] = result['entry'].fillna(False).astype(bool) & valid
    return result


def run_experiment(candles: pd.DataFrame, config: Config) -> Experiment:
    interval_ms = ccxt.Exchange.parse_timeframe(config.market.timeframe) * 1000
    data = validate_candles(candles, interval_ms)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    if int(data.timestamp.iloc[-1].timestamp() * 1000) + interval_ms > now_ms:
        raise ValueError('Input contains an unclosed candle')
    split = int(len(data) * config.backtest.in_sample_fraction)
    if split < 2 or len(data) - split < 2:
        raise ValueError('Need at least two candles in each segment')
    # Compute the indicator on the first segment only, not on held-out rows.
    in_sample = data.iloc[:split].copy()
    signals = EMATrendStrategy(config.strategy).signals(in_sample)
    modified = apply_cost_filter(signals, config)
    # Last signal has no next open within this independent segment.
    original_count = int(signals['entry'].iloc[:-1].sum())
    modified_count = int(modified['entry'].iloc[:-1].sum())
    return Experiment(simulate(signals, config), simulate(modified, config),
                      original_count, original_count - modified_count, split, len(data))


def _fmt(value):
    return 'N/A' if value is None else f'{value:.6g}'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', required=True, help='Existing backtest folder containing candles.csv and config_used.yaml')
    args = parser.parse_args(argv)
    path = Path(args.report)
    config = load_config(path / 'config_used.yaml')
    interval_ms = ccxt.Exchange.parse_timeframe(config.market.timeframe) * 1000
    candles = load_csv(path / 'candles.csv', interval_ms)
    experiment = run_experiment(candles, config)
    per_year = 365.25 * 86400 / ccxt.Exchange.parse_timeframe(config.market.timeframe)
    baseline = calculate_metrics(experiment.baseline, per_year)
    filtered = calculate_metrics(experiment.filtered, per_year)
    print('EXPLORATORY ONLY — IN-SAMPLE; no trading or bot setting changed')
    print('Period UTC:', experiment.baseline.start.isoformat(), 'to', experiment.baseline.end.isoformat())
    print('Candles in-sample / total:', experiment.in_sample_candles, '/', experiment.total_candles)
    print('Cost/ATR stop-distance threshold:', MAX_COST_TO_STOP)
    print('Executable entry signals before filter:', experiment.eligible_signals)
    print('Entry signals blocked by filter:', experiment.blocked_signals)
    print('Metric | Baseline | Cost filter')
    for name in ('trade_count', 'total_return', 'max_drawdown', 'win_rate', 'profit_factor', 'expectancy_idr'):
        print(f"{name} | {_fmt(baseline[name])} | {_fmt(filtered[name])}")
    print('Kill activated:', experiment.baseline.killed, '|', experiment.filtered.killed)
    print('Return and drawdown are fractions; results include assumed fee/tax/slippage.')
    print('Estimate uses signal-close price; spread and live fills are not modeled.')
    print('Not a blind test, not evidence of future performance, and not permission to trade live.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
