"""Local spot-only ledger; no private API calls or automatic trading loop."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import json
import math
from pathlib import Path
import sqlite3
from typing import Any, Mapping

from src.config import Config
from src.risk.manager import RiskState, evaluate, position_size


class OrderRejected(ValueError):
    """The simulator rejected an order without changing balances."""


@dataclass(frozen=True)
class Quote:
    bid: float
    ask: float
    last: float
    timestamp_ms: int
    candle_close_ms: int
    candle_close_price: float


class PaperExecutor:
    """Transactional IDR account. Quotes and ccxt market metadata come from caller.

    The caller must provide fresh quotes for *all* open positions. On every order
    risk is persisted before processing; a kill blocks buys but permits sells.
    A controlling loop must monitor risk and close positions on kill.
    """
    def __init__(self, path: str | Path, exchange: Any, config: Config, initial_cash: float | None = None):
        self.exchange, self.config = exchange, config
        capital = config.backtest.initial_equity_idr if initial_cash is None else initial_cash
        if not math.isfinite(capital) or capital <= 0:
            raise ValueError('Invalid initial paper capital')
        if str(path) != ':memory:':
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), timeout=10, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA busy_timeout = 10000')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS account (id INTEGER PRIMARY KEY CHECK(id=1), cash REAL NOT NULL);
          CREATE TABLE IF NOT EXISTS positions (symbol TEXT PRIMARY KEY, amount REAL NOT NULL, entry_price REAL NOT NULL, stop REAL NOT NULL);
          CREATE TABLE IF NOT EXISTS orders (client_id TEXT PRIMARY KEY, request TEXT NOT NULL, result TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS risk_state (id INTEGER PRIMARY KEY CHECK(id=1), day TEXT NOT NULL, day_start REAL NOT NULL, peak REAL NOT NULL, killed INTEGER NOT NULL, daily_halted INTEGER NOT NULL);
        ''')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            columns = {row['name'] for row in self.db.execute('PRAGMA table_info(positions)')}
            if 'entry_id' not in columns:
                self.db.execute("ALTER TABLE positions ADD COLUMN entry_id TEXT")
                self.db.execute("UPDATE positions SET entry_id='legacy:' || symbol WHERE entry_id IS NULL")
            if 'target' not in columns:
                self.db.execute("ALTER TABLE positions ADD COLUMN target REAL")
                self.db.execute("UPDATE positions SET target=entry_price+(entry_price-stop)*? WHERE target IS NULL", (config.strategy.risk_reward,))
            account = self.db.execute('SELECT cash FROM account WHERE id=1').fetchone()
            state = self.db.execute('SELECT id FROM risk_state WHERE id=1').fetchone()
            if account is None and state is None:
                self.db.execute('INSERT INTO account VALUES (1, ?)', (capital,))
                self.db.execute('INSERT INTO risk_state VALUES (1, ?, ?, ?, 0, 0)',
                                (datetime.now(timezone.utc).date().isoformat(), capital, capital))
            elif account is None or state is None:
                raise ValueError('Incomplete paper account state; manual reconciliation required')
            self.db.execute('COMMIT')
        except Exception:
            self.db.execute('ROLLBACK')
            self.db.close()
            raise

    def close(self) -> None:
        self.db.close()

    def balances(self) -> dict:
        return {'IDR': self.db.execute('SELECT cash FROM account WHERE id=1').fetchone()['cash'],
                'positions': {r['symbol']: dict(r) for r in self.db.execute('SELECT * FROM positions')}}

    def remaining_entry_capital(self) -> float:
        """Cost-basis cap across open paper positions, including entry fees.

        Open positions created before reliable entry records existed cannot be
        valued safely: block new buys, but do not prevent sells. Caller sizing
        is advisory; place_order repeats this check inside its write transaction.
        """
        costs = []
        rows = self.db.execute("""SELECT p.symbol, p.amount, p.entry_price, p.entry_id, o.result
            FROM positions p LEFT JOIN orders o ON o.client_id=p.entry_id""")
        for row in rows:
            try:
                result = json.loads(row['result'])
                amount = float(row['amount'])
                price = float(row['entry_price'])
                fee = float(result['fee_tax'])
                if (result['client_id'] != row['entry_id'] or result['side'] != 'buy'
                        or result['status'] != 'closed' or result['symbol'] != row['symbol']
                        or float(result['amount']) != amount
                        or float(result['price']) != price or not all(math.isfinite(v) for v in (amount, price, fee))
                        or amount <= 0 or price <= 0 or fee < 0):
                    raise ValueError('Invalid entry record')
                cost = amount * price + fee
                if not math.isfinite(cost) or cost <= 0:
                    raise ValueError('Invalid position cost')
                costs.append(cost)
            except (TypeError, ValueError, KeyError, OverflowError) as exc:
                raise OrderRejected('Open position entry cost unavailable; new buys blocked') from exc
        try:
            spent = math.fsum(costs)
        except OverflowError as exc:
            raise OrderRejected('Open position entry cost unavailable; new buys blocked') from exc
        if not math.isfinite(spent):
            raise OrderRejected('Open position entry cost unavailable; new buys blocked')
        return max(0.0, self.config.risk.max_capital_idr - spent)

    def _validate_quote(self, q: Quote, now_ms: int, entry: bool = False) -> None:
        checks = (q.bid, q.ask, q.last, q.candle_close_price)
        cfg = self.config.execution
        if not all(math.isfinite(x) and x > 0 for x in checks) or q.bid > q.ask:
            raise OrderRejected('Invalid bid/ask/last')
        if q.timestamp_ms > now_ms or now_ms - q.timestamp_ms > cfg.max_quote_age_seconds * 1000:
            raise OrderRejected('Ticker stale or from the future')
        interval_ms = self.exchange.parse_timeframe(self.config.market.timeframe) * 1000
        if q.candle_close_ms > now_ms or now_ms - q.candle_close_ms > interval_ms * cfg.max_candle_age_intervals:
            raise OrderRejected('Candle stale or not closed')
        if (q.ask - q.bid) / ((q.ask + q.bid) / 2) > cfg.max_spread_rate:
            raise OrderRejected('Spread too wide')
        if max(abs(q.ask / q.last - 1), abs(q.bid / q.last - 1)) > cfg.max_price_deviation_rate:
            raise OrderRejected('Ticker price deviation too large')
        # A gap away from candle close blocks entries, not risk valuation or exits.
        if entry and abs(q.candle_close_price / q.last - 1) > cfg.max_price_deviation_rate:
            raise OrderRejected('Candle price deviation too large for entry')

    def _risk(self, quotes: Mapping[str, Quote], now_ms: int):
        cash = self.db.execute('SELECT cash FROM account WHERE id=1').fetchone()['cash']
        positions = list(self.db.execute('SELECT * FROM positions'))
        equity = cash
        for position in positions:
            symbol = position['symbol']
            if symbol not in quotes:
                raise OrderRejected('Missing mark quote for open position')
            q = quotes[symbol]
            self._validate_quote(q, now_ms)
            cfg = self.config.execution
            equity += position['amount'] * q.bid * (1 - cfg.slippage_rate) * (1 - cfg.taker_sell_fee_rate - cfg.sell_tax_rate)
        from datetime import date
        stored = self.db.execute('SELECT * FROM risk_state WHERE id=1').fetchone()
        state = RiskState(date.fromisoformat(stored['day']), stored['day_start'], stored['peak'], bool(stored['killed']), bool(stored['daily_halted']))
        decision = evaluate(state, equity, datetime.fromtimestamp(now_ms / 1000, timezone.utc).date(), self.config.risk)
        new = decision.state
        self.db.execute('UPDATE risk_state SET day=?, day_start=?, peak=?, killed=?, daily_halted=? WHERE id=1',
                        (new.day.isoformat(), new.day_start_equity, new.peak_equity, int(new.killed), int(new.daily_halted)))
        return decision, equity

    def assess_risk(self, quotes: Mapping[str, Quote], now_ms: int):
        """Persist a fresh risk snapshot; caller must react to sticky kill."""
        self.db.execute('BEGIN IMMEDIATE')
        try:
            decision, equity = self._risk(quotes, now_ms)
            self.db.execute('COMMIT')
            return decision, equity
        except Exception:
            self.db.execute('ROLLBACK')
            raise

    def place_order(self, client_id: str, symbol: str, side: str, amount: float,
                    stop: float | None, quotes: Mapping[str, Quote], now_ms: int) -> dict:
        """Immediately simulate a taker fill. Repeated identical client IDs are idempotent.

        Sell must close the entire position. Insufficient metadata fails closed.
        The loop is responsible for evaluating exits and kill liquidation.
        """
        if not client_id or side not in ('buy', 'sell') or symbol not in self.config.market.pairs:
            raise OrderRejected('Invalid client ID, side, or pair')
        if not math.isfinite(amount) or amount <= 0 or (stop is not None and not math.isfinite(stop)):
            raise OrderRejected('Invalid order amount or stop')
        request = json.dumps([symbol, side, amount, stop], allow_nan=False, separators=(',', ':'))
        self.db.execute('BEGIN IMMEDIATE')
        committed = False
        risk_saved = False
        try:
            previous = self.db.execute('SELECT request, result FROM orders WHERE client_id=?', (client_id,)).fetchone()
            if previous:
                if previous['request'] != request:
                    raise OrderRejected('client_id already used for a different order')
                self.db.execute('COMMIT'); committed = True
                return json.loads(previous['result'])
            decision, equity = self._risk(quotes, now_ms)
            self.db.execute("SAVEPOINT order_changes")
            risk_saved = True
            # Serialize dashboard/Telegram pause and stop against order placement.
            bot_state_exists = self.db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='bot_state'"
            ).fetchone()
            if side == 'buy' and bot_state_exists:
                flags = {r['key']: r['value'] for r in self.db.execute(
                    "SELECT key,value FROM bot_state WHERE key IN ('paused','stopping')"
                )}
                if flags.get('paused', '0') == '1' or flags.get('stopping', '0') != '0':
                    self.db.execute('COMMIT'); committed = True
                    raise OrderRejected('Paused or stopping; entry blocked')
            # Save the risk state even if the order is rejected due to a limit.
            if side == 'buy' and not decision.allow_entry:
                self.db.execute('COMMIT'); committed = True
                raise OrderRejected('Risk limit blocks new entry')
            if symbol not in quotes:
                raise OrderRejected('Missing order quote')
            q = quotes[symbol]
            self._validate_quote(q, now_ms, entry=side == 'buy')
            market = self.exchange.market(symbol)
            if market.get('spot') is not True or market.get('active') is not True or market.get('quote') != 'IDR':
                raise OrderRejected('Market must be active IDR spot')
            min_amount = market.get('limits', {}).get('amount', {}).get('min')
            if min_amount is None or not math.isfinite(float(min_amount)) or min_amount <= 0:
                raise OrderRejected('Exchange minimum amount unavailable')
            precision_amount = self.exchange.amount_to_precision(symbol, amount)
            if Decimal(str(amount)) != Decimal(str(precision_amount)):
                raise OrderRejected('Amount violates exchange precision')
            if amount < min_amount:
                raise OrderRejected('Below minimum order amount')
            cfg = self.config.execution
            price = (q.ask * (1 + cfg.slippage_rate) if side == 'buy' else q.bid * (1 - cfg.slippage_rate))
            # Simulator price is a market fill, not a limit price sent to exchange.
            # CCXT's Indodax adapter maps trade_min_base_currency to price.min;
            # its meaning is not documented clearly enough to use as a price bound.
            cost_limits = market.get('limits', {}).get('cost', {}) or {}
            minimum_cost = cost_limits.get('min')
            configured_minimum = cfg.min_notional_idr.get(symbol)
            if minimum_cost is None and configured_minimum is None:
                raise OrderRejected('Minimum notional unknown; configure verified minimum')
            if configured_minimum is not None:
                minimum_cost = max(minimum_cost or 0, configured_minimum)
            for value, limits in [(price * amount, {'min': minimum_cost, 'max': cost_limits.get('max')})]:
                if limits.get('min') is not None and value < limits['min']:
                    raise OrderRejected('Below exchange price/cost minimum')
                if limits.get('max') is not None and value > limits['max']:
                    raise OrderRejected('Above exchange price/cost maximum')
            amount_max = market.get('limits', {}).get('amount', {}).get('max')
            if amount_max is not None and amount > amount_max:
                raise OrderRejected('Above exchange amount maximum')
            rate = (cfg.taker_buy_fee_rate + cfg.buy_tax_rate if side == 'buy'
                    else cfg.taker_sell_fee_rate + cfg.sell_tax_rate)
            gross, fee = amount * price, amount * price * rate
            if not math.isfinite(gross) or not math.isfinite(fee):
                raise OrderRejected('Non-finite order value')
            cash = self.db.execute('SELECT cash FROM account WHERE id=1').fetchone()['cash']
            existing = self.db.execute('SELECT * FROM positions WHERE symbol=?', (symbol,)).fetchone()
            if side == 'buy':
                if existing:
                    raise OrderRejected('Position already open; averaging down prohibited')
                # This second, transactional check is authoritative across all pairs.
                if gross + fee > self.remaining_entry_capital():
                    raise OrderRejected('Maximum invested capital exceeded')
                if stop is None or not 0 < stop < price:
                    raise OrderRejected('Valid stop required')
                max_amount = position_size(equity, price, stop, self.config.risk.risk_per_trade,
                                           cfg.taker_buy_fee_rate, cfg.buy_tax_rate, cfg.slippage_rate, cash,
                                           sell_fee_rate=cfg.taker_sell_fee_rate, sell_tax_rate=cfg.sell_tax_rate)
                if amount > max_amount * (1 + 1e-12) or gross + fee > cash:
                    raise OrderRejected('Risk budget or cash exceeded')
                self.db.execute('UPDATE account SET cash=? WHERE id=1', (cash - gross - fee,))
                self.db.execute('INSERT INTO positions (symbol, amount, entry_price, stop, entry_id, target) VALUES (?, ?, ?, ?, ?, ?)',
                                (symbol, amount, price, stop, client_id, price + (price - stop) * self.config.strategy.risk_reward))
            else:
                if not existing or Decimal(str(amount)) != Decimal(str(existing['amount'])):
                    raise OrderRejected('Sell must close an existing position fully; shorting forbidden')
                self.db.execute('UPDATE account SET cash=? WHERE id=1', (cash + gross - fee,))
                self.db.execute('DELETE FROM positions WHERE symbol=?', (symbol,))
            result = {'client_id': client_id, 'symbol': symbol, 'side': side, 'amount': amount,
                      'price': price, 'fee_tax': fee, 'status': 'closed', 'timestamp_ms': now_ms}
            self.db.execute('INSERT INTO orders VALUES (?, ?, ?)', (client_id, request, json.dumps(result)))
            self.db.execute('COMMIT'); committed = True
            return result
        except Exception:
            if not committed:
                if risk_saved:
                    self.db.execute('ROLLBACK TO order_changes')
                    self.db.execute('COMMIT')
                else:
                    self.db.execute('ROLLBACK')
            raise
