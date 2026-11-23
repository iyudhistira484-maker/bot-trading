"""Backtest metrics; undefined values are represented as None."""
import math

import numpy as np

from src.backtest.engine import Result


def calculate_metrics(result: Result, periods_per_year: float) -> dict:
    eq = result.equity.equity.astype(float)
    returns = eq.pct_change().dropna()
    years = (result.end - result.start).total_seconds() / (365.25 * 86400)
    total = eq.iloc[-1] / result.initial_equity - 1
    annual_log = math.log(eq.iloc[-1] / result.initial_equity) / years if years > 0 and eq.iloc[-1] > 0 else None
    cagr = math.expm1(annual_log) if annual_log is not None and annual_log < 700 else None
    std = returns.std(ddof=1)
    downside = math.sqrt(float((np.minimum(returns, 0) ** 2).mean())) if len(returns) else 0
    pnls = result.trades.pnl.astype(float)
    profit = pnls[pnls > 0].sum()
    loss = -pnls[pnls < 0].sum()
    return {
        'total_return': float(total), 'cagr': float(cagr) if cagr is not None and math.isfinite(cagr) else None,
        'max_drawdown': float((eq / eq.cummax() - 1).min()),
        'sharpe': float(returns.mean() / std * math.sqrt(periods_per_year)) if len(returns) > 1 and std > 0 else None,
        'sortino': float(returns.mean() / downside * math.sqrt(periods_per_year)) if downside > 0 else None,
        'win_rate': float((pnls > 0).mean()) if len(pnls) else None,
        'profit_factor': float(profit / loss) if loss > 0 else None,
        'trade_count': len(pnls),
        'avg_duration_hours': float(result.trades.duration_hours.mean()) if len(pnls) else None,
        'expectancy_idr': float(pnls.mean()) if len(pnls) else None,
    }
