import subprocess
import sys


def test_backtest_cli_end_to_end(candles, tmp_path):
    csv = tmp_path / 'input.csv'
    output = tmp_path / 'report'
    candles.to_csv(csv, index=False)
    result = subprocess.run([sys.executable, '-m', 'src.backtest.run', '--csv', str(csv), '--output', str(output)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    for file in ['candles.csv', 'config_used.yaml', 'in_sample_trades.csv', 'out_of_sample_trades.csv',
                 'in_sample_equity.csv', 'out_of_sample_equity.csv', 'equity_curve.png', 'summary.md']:
        assert (output / file).is_file()


def test_main_backtest_dispatch(candles, tmp_path):
    csv = tmp_path / 'input.csv'
    output = tmp_path / 'report'
    candles.to_csv(csv, index=False)
    result = subprocess.run([sys.executable, '-m', 'src.main', 'backtest', '--csv', str(csv), '--output', str(output)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert (output / 'summary.md').is_file()
