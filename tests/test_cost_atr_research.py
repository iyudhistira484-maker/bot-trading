import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

from src.research.cost_atr import MAX_COST_TO_STOP, apply_cost_filter, run_experiment


def test_filter_blocks_when_estimated_cost_is_large_relative_to_stop(config):
    frame = pd.DataFrame({'close': [100., 100., 100., 100., 100.],
                          'atr': [10., .1, 0., np.nan, 10.],
                          'entry': [True, True, True, True, False],
                          'exit': [False, True, False, True, True]})
    before = frame.copy(deep=True)
    result = apply_cost_filter(frame, config)
    assert result.entry.tolist() == [True, False, False, False, False]
    assert result.exit.tolist() == frame.exit.tolist()
    pd.testing.assert_frame_equal(frame, before)


def test_cost_uses_closed_price_and_respects_threshold(config):
    config.execution.slippage_rate = 0
    config.execution.taker_buy_fee_rate = .01
    config.execution.buy_tax_rate = 0
    config.execution.taker_sell_fee_rate = .01
    config.execution.sell_tax_rate = 0
    config.strategy.atr_stop_multiplier = 2
    # Round-trip estimate is exactly 2; distance 4, so ratio is exactly 0.5.
    frame = pd.DataFrame({'close': [100., 100.], 'atr': [2., 1.99],
                          'entry': [True, True], 'exit': [False, False],
                          'open': [1., 1_000_000.]})
    assert MAX_COST_TO_STOP == .5
    assert apply_cost_filter(frame, config).entry.tolist() == [True, False]
    frame.loc[:, 'open'] = [99999., 1.]
    assert apply_cost_filter(frame, config).entry.tolist() == [True, False]


def test_unusable_values_fail_closed_without_blocking_exits(config):
    frame = pd.DataFrame({'close': [0., np.inf, -1., 100.], 'atr': [1., 1., 1., np.inf],
                          'entry': [True]*4, 'exit': [True]*4})
    result = apply_cost_filter(frame, config)
    assert result.entry.tolist() == [False]*4
    assert result.exit.tolist() == [True]*4


def test_experiment_does_not_use_out_of_sample_prices(config, candles):
    split = int(len(candles) * config.backtest.in_sample_fraction)
    first = run_experiment(candles, config)
    altered = candles.copy()
    for column in ('open', 'high', 'low', 'close'):
        altered.loc[split:, column] *= 10
    second = run_experiment(altered, config)
    pd.testing.assert_frame_equal(first.baseline.trades, second.baseline.trades)
    pd.testing.assert_frame_equal(first.filtered.trades, second.filtered.trades)
    pd.testing.assert_frame_equal(first.baseline.equity, second.baseline.equity)
    pd.testing.assert_frame_equal(first.filtered.equity, second.filtered.equity)
    assert first.eligible_signals == second.eligible_signals
    assert first.blocked_signals == second.blocked_signals
    assert first.in_sample_candles == split


def test_cli_only_reads_saved_report_and_does_not_write(config, candles, tmp_path):
    import yaml
    report = tmp_path / 'report'
    report.mkdir()
    candles.to_csv(report / 'candles.csv', index=False)
    (report / 'config_used.yaml').write_text(yaml.safe_dump(config.model_dump()))
    before = {p.name: p.read_bytes() for p in report.iterdir()}
    run = subprocess.run([sys.executable, '-m', 'src.research.cost_atr', '--report', str(report)],
                         capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    assert 'EXPLORATORY ONLY' in run.stdout
    assert 'Not a blind test' in run.stdout
    assert {p.name: p.read_bytes() for p in report.iterdir()} == before


def test_empty_or_gapped_history_rejected(config, candles):
    with pytest.raises(ValueError): run_experiment(candles.iloc[:0], config)
    with pytest.raises(ValueError, match='Gap'): run_experiment(candles.drop(index=10), config)


def test_baseline_reproduces_unmodified_backtest_in_sample(config, candles):
    from src.backtest.engine import run_split
    from src.strategy.ema import EMATrendStrategy
    expected, _ = run_split(candles, EMATrendStrategy(config.strategy), config)
    research = run_experiment(candles, config)
    pd.testing.assert_frame_equal(research.baseline.trades, expected.trades)
    pd.testing.assert_frame_equal(research.baseline.equity, expected.equity)


def test_filtered_signal_changes_only_simulated_entries(config):
    from src.backtest.engine import simulate
    frame = pd.DataFrame({
        'timestamp': pd.date_range('2023-01-01', periods=3, freq='h', tz='UTC'),
        'open': [100., 100., 100.], 'high': [101., 101., 101.],
        'low': [99., 99., 99.], 'close': [100., 100., 100.],
        'atr': [.1, .1, .1], 'entry': [True, False, False], 'exit': [False, False, False],
    })
    filtered = apply_cost_filter(frame, config)
    assert len(simulate(frame, config).trades) == 1
    assert len(simulate(filtered, config).trades) == 0
