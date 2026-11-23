from datetime import date, timedelta
import pytest

from src.risk.manager import RiskState, evaluate, position_size


def test_position_size_includes_cost_and_cash():
    amount = position_size(10000, 100, 90, .01, .001, .002, .001, 10000)
    loss_per_unit = 100 * 1.003 - 90 * .999 * .997
    assert amount * loss_per_unit == pytest.approx(100)
    assert position_size(10000, 100, 90, .01, available_cash=100) == 1
    for args in [(100, 100, 100, .01), (100, 100, 90, .021), (float('nan'), 100, 90, .01)]:
        with pytest.raises(ValueError): position_size(*args)


def test_daily_halt_sticky_until_next_day_and_kill_sticky(config):
    today = date(2025, 1, 1)
    state = RiskState(today, 1000, 1000)
    daily = evaluate(state, 970, today, config.risk)
    assert daily.daily_loss_reached and not daily.allow_entry and not daily.state.killed
    rebound = evaluate(daily.state, 1000, today, config.risk)
    assert not rebound.allow_entry
    new_day = evaluate(rebound.state, 1000, today + timedelta(days=1), config.risk)
    assert new_day.allow_entry
    kill = evaluate(new_day.state, 900, today + timedelta(days=1), config.risk)
    assert kill.kill_triggered and kill.state.killed
    recovery = evaluate(kill.state, 1000, today + timedelta(days=2), config.risk)
    assert not recovery.allow_entry and recovery.state.killed and not recovery.kill_triggered
    with pytest.raises(ValueError): evaluate(recovery.state, 1000, today, config.risk)


def test_peak_trailing_drawdown(config):
    today = date(2025, 1, 1)
    raised = evaluate(RiskState(today, 1000, 1000), 1200, today, config.risk)
    assert raised.state.peak_equity == 1200
    assert evaluate(raised.state, 1080, today, config.risk).state.killed
