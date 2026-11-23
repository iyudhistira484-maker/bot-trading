"""Write reproducible split metrics and visualizations."""
from pathlib import Path

import ccxt
import yaml
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from src.backtest.engine import Result
from src.backtest.metrics import calculate_metrics
from src.config import Config


def write_report(in_sample: Result, out_sample: Result, config: Config, path: str | Path) -> None:
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    periods_per_year = 365.25 * 86400 / ccxt.Exchange.parse_timeframe(config.market.timeframe)
    inside = calculate_metrics(in_sample, periods_per_year)
    outside = calculate_metrics(out_sample, periods_per_year)
    (path / "config_used.yaml").write_text(yaml.safe_dump(config.model_dump()), encoding="utf-8")
    for name, result in [('in_sample', in_sample), ('out_of_sample', out_sample)]:
        result.trades.to_csv(path / f'{name}_trades.csv', index=False)
        result.equity.to_csv(path / f'{name}_equity.csv', index=False)
    fig, ax = plt.subplots()
    for label, result in [('In-sample', in_sample), ('Out-of-sample', out_sample)]:
        ax.plot(result.equity.timestamp, result.equity.equity, label=label)
    ax.set_ylabel('Equity (IDR)')
    ax.legend()
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(path / 'equity_curve.png')
    plt.close(fig)
    lines = ['# Backtest report', '', 'Independent segments: both start with the same capital; indicators use earlier candles for warmup.',
             'Signals execute at the next open; bar-level stop is pessimistic when stop and target both hit.',
             'Costs: configurable taker fee and tax separately on buy and sell, plus slippage; check configured rates. No liquidity or order-book model.',
             'Daily loss blocks entries until the next UTC day. Drawdown kill is sticky; positions are liquidated at the next open after a close-based breach. Intrabar breaches are not observed.', '',
             '| Metric | In-sample | Out-of-sample |', '|---|---:|---:|']
    for key in inside:
        def fmt(value):
            return 'N/A' if value is None else f'{value:.6g}'
        lines.append(f'| {key} | {fmt(inside[key])} | {fmt(outside[key])} |')
    lines += ['', f'Kill switch activated: in-sample={in_sample.killed}, out-of-sample={out_sample.killed}.']
    lines += ['', 'Returns and drawdown are fractions. Sharpe and Sortino assume zero risk-free rate; profit factor is N/A without losses.']
    if inside['total_return'] > 0 and outside['total_return'] < inside['total_return'] * config.backtest.overfit_return_ratio:
        lines += ['', '**Warning: possible overfit** — out-of-sample return is substantially below in-sample return. Different segment lengths also affect total return.']
    if not inside['trade_count'] or not outside['trade_count']:
        lines += ['', '**Warning:** at least one segment has no trades; performance is inconclusive.']
    (path / 'summary.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
