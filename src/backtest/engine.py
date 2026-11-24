"""Single-pair, long-only OHLCV simulation; no claim of live execution parity."""
from dataclasses import dataclass
from datetime import datetime, timezone
import math

import pandas as pd
import ccxt

from src.config import Config
from src.data.ohlcv import validate_candles
from src.strategy.base import Strategy
from src.risk.manager import RiskState, evaluate, position_size


@dataclass
class Result:
    trades: pd.DataFrame
    equity: pd.DataFrame
    start: pd.Timestamp
    end: pd.Timestamp
    initial_equity: float
    killed: bool = False


TRADE_COLUMNS = ['entry_time', 'exit_time', 'entry_price', 'exit_price', 'amount',
                 'entry_fee_tax', 'exit_fee_tax', 'pnl', 'return_pct', 'duration_hours', 'exit_reason']


def simulate(signals: pd.DataFrame, config: Config, start: int = 0, end: int | None = None) -> Result:
    """Trade only in [start, end); signal from prior close executes at next open.

    Exit signals execute at open. Otherwise stop/target are checked against the
    bar, with stop first if both hit. Gap fills occur at the open. Final open
    position is liquidated at the final close. Indicators may have prior warmup.
    Risk is marked on bar close; a kill-triggered liquidation occurs next open.
    Gaps can make losses larger than planned.
    """
    end = len(signals) if end is None else end
    if not 0 <= start < end <= len(signals):
        raise ValueError('Empty or invalid simulation range')
    interval_seconds = ccxt.Exchange.parse_timeframe(config.market.timeframe)
    if not interval_seconds:
        raise ValueError("Invalid timeframe")
    interval = pd.Timedelta(seconds=interval_seconds)
    cfg, risk = config.execution, config.risk
    buy_rate = cfg.taker_buy_fee_rate + cfg.buy_tax_rate
    sell_rate = cfg.taker_sell_fee_rate + cfg.sell_tax_rate
    slip = cfg.slippage_rate
    cash = config.backtest.initial_equity_idr
    initial = cash
    position = None
    trades: list[dict] = []
    equity: list[dict] = []
    risk_state = RiskState(signals.iloc[start].timestamp.date(), initial, initial)
    allow_entry = True
    previous_equity = initial

    def close_position(raw_price: float, ts: pd.Timestamp, reason: str) -> None:
        nonlocal cash, position
        price = raw_price * (1 - slip)
        gross = position['amount'] * price
        fee = gross * sell_rate
        cash += gross - fee
        pnl = gross - fee - position['cost']
        trades.append(dict(entry_time=position['time'], exit_time=ts,
                           entry_price=position['price'], exit_price=price,
                           amount=position['amount'], entry_fee_tax=position['fee'],
                           exit_fee_tax=fee, pnl=pnl, return_pct=pnl / position['cost'],
                           duration_hours=(ts - position['time']).total_seconds() / 3600,
                           exit_reason=reason))
        position = None

    for i in range(start, end):
        row = signals.iloc[i]
        # Reset the daily baseline at the UTC boundary using the last available close.
        today = row.timestamp.date()
        if today != risk_state.day:
            decision = evaluate(risk_state, previous_equity, today, risk)
            risk_state, allow_entry = decision.state, decision.allow_entry
        # A segment does not inherit an order or position from another segment.
        prev = signals.iloc[i - 1] if i > start else None
        if position and risk_state.killed:
            close_position(row['open'], row.timestamp, 'kill_switch')
        if position and prev is not None and bool(prev['exit']):
            close_position(row['open'], row.timestamp, 'signal')
        if position is None and allow_entry and not risk_state.killed and prev is not None and bool(prev['entry']):
            atr = float(prev['atr'])
            entry = row['open'] * (1 + slip)
            stop = entry - atr * config.strategy.atr_stop_multiplier
            if math.isfinite(atr) and atr > 0 and stop > 0:
                target = entry + (entry - stop) * config.strategy.risk_reward
                # One position at a time: its entry cost, including buy fees,
                # must fit the configured capital cap as well as available cash.
                budget = min(cash, math.nextafter(risk.max_capital_idr, 0.0))
                qty = position_size(cash, entry, stop, risk.risk_per_trade, cfg.taker_buy_fee_rate,
                                    cfg.buy_tax_rate, slip, budget,
                                    sell_fee_rate=cfg.taker_sell_fee_rate, sell_tax_rate=cfg.sell_tax_rate)
                if qty > 0:
                    fee = qty * entry * buy_rate
                    cost = qty * entry + fee
                    cash -= cost
                    position = dict(time=row.timestamp, price=entry, amount=qty, stop=stop,
                                    target=target, fee=fee, cost=cost)
        if position:
            if row['open'] <= position['stop']:
                close_position(row['open'], row.timestamp, 'stop_gap')
            elif row['open'] >= position['target']:
                close_position(row['open'], row.timestamp, 'target_gap')
            elif row['low'] <= position['stop']:
                close_position(position['stop'], row.timestamp, 'stop')
            elif row['high'] >= position['target']:
                close_position(position['target'], row.timestamp, 'target')
        if i == end - 1 and position:
            # Candle timestamp denotes open; exit is at its close.
            close_position(row['close'], row.timestamp + interval, 'end_of_segment')
        mark = cash if not position else cash + position['amount'] * row['close'] * (1 - slip) * (1 - sell_rate)
        decision = evaluate(risk_state, mark, today, risk)
        risk_state, allow_entry = decision.state, decision.allow_entry
        previous_equity = mark
        equity.append({'timestamp': row.timestamp + interval, 'equity': mark})
    # Add initial capital so first-bar costs count toward drawdown and returns.
    equity.insert(0, {'timestamp': signals.iloc[start].timestamp, 'equity': initial})
    return Result(pd.DataFrame(trades, columns=TRADE_COLUMNS), pd.DataFrame(equity),
                  signals.iloc[start].timestamp, signals.iloc[end - 1].timestamp + interval, initial, risk_state.killed)


def run_split(candles: pd.DataFrame, strategy: Strategy, config: Config) -> tuple[Result, Result]:
    interval_ms = ccxt.Exchange.parse_timeframe(config.market.timeframe) * 1000
    if not interval_ms:
        raise ValueError('Invalid timeframe')
    df = validate_candles(candles, interval_ms)
    now = pd.Timestamp(datetime.now(timezone.utc))
    if df.timestamp.iloc[-1] + pd.Timedelta(milliseconds=interval_ms) > now:
        raise ValueError('Backtest data contains an unclosed candle')
    split = int(len(df) * config.backtest.in_sample_fraction)
    if split < 2 or len(df) - split < 2:
        raise ValueError('Need at least two candles in each split')
    signals = strategy.signals(df)
    return simulate(signals, config, 0, split), simulate(signals, config, split, len(df))
