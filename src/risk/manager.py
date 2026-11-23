"""Pure risk calculations; state must be persisted by the caller."""
from dataclasses import dataclass
from datetime import date
import math

from src.config import RiskConfig


def position_size(equity: float, entry: float, stop: float, risk_fraction: float,
                  fee_rate: float = 0.0, tax_rate: float = 0.0,
                  slippage_rate: float = 0.0, available_cash: float | None = None) -> float:
    """Maximum base amount using executed entry and estimated stop proceeds.

    Gap risk means actual losses can exceed the budget. Tax is modeled per side.
    """
    values = (equity, entry, stop, risk_fraction, fee_rate, tax_rate, slippage_rate)
    if not all(math.isfinite(v) for v in values):
        raise ValueError('Non-finite risk input')
    if equity <= 0 or entry <= 0 or not 0 < stop < entry or not 0 < risk_fraction <= 0.02:
        raise ValueError('Invalid equity, entry, stop or risk fraction')
    if min(fee_rate, tax_rate, slippage_rate) < 0 or fee_rate + tax_rate >= 1 or slippage_rate >= 1:
        raise ValueError('Invalid costs')
    cash = equity if available_cash is None else available_cash
    if not math.isfinite(cash) or cash < 0:
        raise ValueError('Invalid available cash')
    rate = fee_rate + tax_rate
    unit_cost = entry * (1 + rate)
    loss = unit_cost - stop * (1 - slippage_rate) * (1 - rate)
    return max(0.0, min(equity * risk_fraction / loss, cash / unit_cost))


@dataclass(frozen=True)
class RiskState:
    day: date
    day_start_equity: float
    peak_equity: float
    killed: bool = False
    daily_halted: bool = False


@dataclass(frozen=True)
class RiskDecision:
    state: RiskState
    allow_entry: bool
    daily_loss_reached: bool
    kill_triggered: bool


def evaluate(state: RiskState, equity: float, today: date, config: RiskConfig) -> RiskDecision:
    """UTC daily reset on first valuation; kill is sticky until manual reset.

    Restarting alone must not erase the kill state or high-water mark.
    """
    if not math.isfinite(equity) or equity < 0:
        raise ValueError('Invalid equity')
    if not math.isfinite(state.day_start_equity) or state.day_start_equity < 0 or not math.isfinite(state.peak_equity) or state.peak_equity <= 0:
        raise ValueError('Invalid risk state')
    if today < state.day:
        raise ValueError('Risk clock moved backwards')
    daily_base = state.day_start_equity if today == state.day else equity
    peak = max(state.peak_equity, equity)
    daily = (today == state.day and state.daily_halted) or equity <= daily_base * (1 - config.daily_loss_limit)
    kill = state.killed or equity <= peak * (1 - config.max_drawdown)
    new_state = RiskState(today, daily_base, peak, kill, daily)
    return RiskDecision(new_state, not (daily or kill), daily, kill and not state.killed)
