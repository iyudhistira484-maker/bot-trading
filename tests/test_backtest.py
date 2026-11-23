import pandas as pd
import pytest

from src.backtest.engine import simulate, run_split
from src.backtest.metrics import calculate_metrics
from src.backtest.report import write_report
from src.strategy.ema import EMATrendStrategy


def make_signals(candles):
    s = candles.iloc[:5].copy()
    s[['open', 'close']] = 100.0
    s['high'] = 101.0; s['low'] = 99.0
    s['atr'] = 5.0; s['entry'] = False; s['exit'] = False
    return s


def test_next_open_fee_sizing_and_exit(candles, config):
    s = make_signals(candles); s.loc[0, 'entry'] = True
    s.loc[1, ['open', 'close', 'high', 'low']] = [110, 110, 111, 109]
    s.loc[2, ['open', 'close', 'high', 'low']] = [110, 110, 111, 109]
    s.loc[2, 'exit'] = True
    s.loc[3, ['open', 'close', 'high', 'low']] = [115, 115, 116, 114]
    result = simulate(s, config)
    t = result.trades.iloc[0]
    assert t.entry_time == s.timestamp.iloc[1] and t.exit_time == s.timestamp.iloc[3]
    assert t.entry_price == pytest.approx(110 * (1 + config.execution.slippage_rate))
    assert t.exit_price == pytest.approx(115 * (1 - config.execution.slippage_rate))
    rate = config.execution.taker_fee_rate + config.execution.tax_rate
    assert t.entry_fee_tax == pytest.approx(t.amount * t.entry_price * rate)
    assert t.exit_fee_tax == pytest.approx(t.amount * t.exit_price * rate)
    assert result.equity.equity.iloc[-1] == pytest.approx(config.backtest.initial_equity_idr + t.pnl)


def test_stop_first_when_both_hit_and_gap(candles, config):
    s = make_signals(candles); s.loc[0, 'entry'] = True
    s.loc[1, ['high', 'low']] = [150, 50]
    result = simulate(s, config)
    assert result.trades.iloc[0].exit_reason == 'stop'
    s.loc[1, ['high', 'low']] = [101, 99]
    s.loc[2, ['open', 'close', 'high', 'low']] = [70, 70, 71, 69]
    result = simulate(s, config)
    assert result.trades.iloc[0].exit_reason == 'stop_gap'
    assert result.trades.iloc[0].exit_price == pytest.approx(70 * (1-config.execution.slippage_rate))


def test_independent_split_and_reports(candles, config, tmp_path):
    a, b = run_split(candles, EMATrendStrategy(config.strategy), config)
    assert a.equity.equity.iloc[0] == b.equity.equity.iloc[0] == config.backtest.initial_equity_idr
    write_report(a, b, config, tmp_path)
    assert (tmp_path / 'summary.md').exists()
    assert (tmp_path / 'equity_curve.png').exists()
    assert 'Drawdown kill' in (tmp_path / 'summary.md').read_text()
    metrics = calculate_metrics(a, 8766)
    assert metrics['max_drawdown'] <= 0


def test_no_signal_or_inherited_split_order(candles, config):
    s = make_signals(candles); s.loc[1, 'entry'] = True
    result = simulate(s, config, start=2, end=5)
    assert result.trades.empty
    assert result.equity.equity.iloc[-1] == config.backtest.initial_equity_idr


def test_rejects_unclosed_backtest_bar(candles, config):
    candles = candles.iloc[:10].copy()
    candles['timestamp'] = pd.date_range(pd.Timestamp.now(tz='UTC').floor('h') - pd.Timedelta(hours=9), periods=10, freq='h')
    with pytest.raises(ValueError, match='unclosed'):
        run_split(candles, EMATrendStrategy(config.strategy), config)


def test_risk_config_rejects_over_two_percent(config):
    from pydantic import ValidationError
    from src.config import Config
    data = config.model_dump()
    data['risk']['risk_per_trade'] = 0.021
    with pytest.raises(ValidationError):
        Config.model_validate(data)


def test_last_position_closed_and_cost_in_drawdown(candles, config):
    s = make_signals(candles); s.loc[3, 'entry'] = True
    result = simulate(s, config)
    assert result.trades.iloc[0].exit_reason == 'end_of_segment'
    assert result.trades.iloc[0].exit_time == s.timestamp.iloc[-1] + pd.Timedelta(hours=1)
    assert calculate_metrics(result, 8766)['max_drawdown'] < 0


def test_backtest_kill_blocks_future_entries(candles, config):
    s = make_signals(candles)
    s.loc[0, 'entry'] = True
    s.loc[0, 'atr'] = .2
    s.loc[1, ['low', 'high']] = [99.8, 100.2]
    s.loc[2, ['open', 'close', 'high', 'low']] = [50, 50, 51, 49]
    s.loc[2, 'entry'] = True
    result = simulate(s, config)
    assert len(result.trades) == 1
    assert result.trades.iloc[0].exit_reason == 'stop_gap'
    assert result.equity.equity.iloc[-1] < config.backtest.initial_equity_idr * .9


def test_kill_liquidates_at_next_open(candles, config):
    s = make_signals(candles)
    s.loc[0, 'entry'] = True
    config.risk.max_drawdown = .0001
    result = simulate(s, config)
    assert result.killed
    assert result.trades.iloc[0].exit_reason == 'kill_switch'
    assert result.trades.iloc[0].exit_time == s.timestamp.iloc[2]
