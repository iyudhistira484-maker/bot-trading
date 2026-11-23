"""Check the documented paper example, not a claim about current exchange fees."""
import pytest
from pydantic import ValidationError
from src.config import Config, load_config


def test_paper_example_rates_and_minimum():
    cfg = load_config()
    e = cfg.execution
    assert e.taker_buy_fee_rate + e.buy_tax_rate == pytest.approx(.0020555)
    assert e.taker_sell_fee_rate + e.sell_tax_rate == pytest.approx(.0041555)
    assert e.maker_buy_fee_rate + e.buy_tax_rate == pytest.approx(.0010555)
    assert e.maker_sell_fee_rate + e.sell_tax_rate == pytest.approx(.0031555)
    assert e.min_notional_idr['BTC/IDR'] == 25000


def test_rejects_invalid_buy_and_sell_total_costs():
    data = load_config().model_dump()
    data['execution']['sell_tax_rate'] = .999
    with pytest.raises(ValidationError):
        Config.model_validate(data)
