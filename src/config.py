"""Validated application configuration; rates are fractions, not percentages."""
from pathlib import Path
from typing import Annotated

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

Rate = Annotated[float, Field(ge=0, lt=1, allow_inf_nan=False)]
Positive = Annotated[float, Field(gt=0, allow_inf_nan=False)]
Period = Annotated[int, Field(ge=2)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Market(StrictModel):
    pairs: list[str] = Field(min_length=1)
    timeframe: str = Field(min_length=1)
    ohlcv_limit: int = Field(gt=0)
    timeout_ms: int = Field(gt=0)
    retries: int = Field(ge=0)
    retry_delay_seconds: float = Field(ge=0, allow_inf_nan=False)


class StrategyConfig(StrictModel):
    ema_fast: Period
    ema_slow: Period
    ema_trend: Period
    rsi_period: Period
    rsi_min: float = Field(ge=0, le=100, allow_inf_nan=False)
    rsi_max: float = Field(ge=0, le=100, allow_inf_nan=False)
    atr_period: Period
    atr_min: float = Field(ge=0, allow_inf_nan=False)
    atr_stop_multiplier: Positive
    risk_reward: Positive

    @model_validator(mode="after")
    def check_order(self):
        if not self.ema_fast < self.ema_slow < self.ema_trend:
            raise ValueError("EMA periods must satisfy fast < slow < trend")
        if self.rsi_min > self.rsi_max:
            raise ValueError("rsi_min must be <= rsi_max")
        return self


class RiskConfig(StrictModel):
    risk_per_trade: float = Field(gt=0, le=0.02, allow_inf_nan=False)
    daily_loss_limit: float = Field(gt=0, le=1, allow_inf_nan=False)
    max_drawdown: float = Field(gt=0, le=1, allow_inf_nan=False)
    max_capital_idr: Positive


class ExecutionConfig(StrictModel):
    live_enabled: bool = False
    maker_buy_fee_rate: Rate
    maker_sell_fee_rate: Rate
    taker_buy_fee_rate: Rate
    taker_sell_fee_rate: Rate
    buy_tax_rate: Rate
    sell_tax_rate: Rate
    slippage_rate: Rate
    max_spread_rate: Rate
    max_price_deviation_rate: Rate
    max_quote_age_seconds: Positive
    max_candle_age_intervals: Positive
    min_notional_idr: dict[str, Positive] = {}

    @model_validator(mode="after")
    def check_costs(self):
        for fee, tax in ((self.maker_buy_fee_rate, self.buy_tax_rate),
                         (self.maker_sell_fee_rate, self.sell_tax_rate),
                         (self.taker_buy_fee_rate, self.buy_tax_rate),
                         (self.taker_sell_fee_rate, self.sell_tax_rate)):
            if fee + tax >= 1:
                raise ValueError("combined fee and tax must be below 100%")
        return self


class RuntimeConfig(StrictModel):
    paper_database: str = Field(min_length=1)
    poll_interval_seconds: Positive
    entry_window_seconds: Positive
    heartbeat_seconds: Positive
    telegram_poll_limit: int = Field(ge=1, le=100)
    log_file: str = Field(min_length=1)
    log_max_bytes: int = Field(gt=0)
    log_backup_count: int = Field(ge=1)


class BacktestConfig(StrictModel):
    initial_equity_idr: Positive
    in_sample_fraction: float = Field(gt=0, lt=1, allow_inf_nan=False)
    overfit_return_ratio: float = Field(ge=0, allow_inf_nan=False)


class Config(StrictModel):
    market: Market
    strategy: StrategyConfig
    risk: RiskConfig
    execution: ExecutionConfig
    backtest: BacktestConfig
    runtime: RuntimeConfig


def load_config(path: str | Path = "config/config.yaml") -> Config:
    with open(path, encoding="utf-8") as f:
        return Config.model_validate(yaml.safe_load(f))
