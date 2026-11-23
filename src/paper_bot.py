"""Paper-only orchestration. Never calls a private or live order endpoint."""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

from src.config import Config
from src.execution.paper import OrderRejected, PaperExecutor
from src.risk.manager import position_size
from src.strategy.ema import EMATrendStrategy
from src.utils.logging import event


class PaperBot:
    def __init__(self, executor: PaperExecutor, data, config: Config, logger: logging.Logger, telegram=None, clock=time.time):
        self.executor, self.data, self.config, self.log, self.telegram = executor, data, config, logger, telegram
        self.clock = clock
        self._stop_requested = False
        self.strategy = EMATrendStrategy(config.strategy)
        self.db = executor.db
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS bot_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS bot_seen (symbol TEXT NOT NULL, candle_ms INTEGER NOT NULL, PRIMARY KEY(symbol, candle_ms));
        ''')

    def request_stop(self) -> None:
        """Signal-safe request: persistence and liquidation happen in the loop."""
        self._stop_requested = True

    def _persist_stop_request(self) -> None:
        if self._stop_requested and self._get('stopping', '0') != '1':
            self._set('stopping', '1')
            event(self.log, 'stop_requested', source='signal')

    def _get(self, key: str, default: str = '') -> str:
        row = self.db.execute('SELECT value FROM bot_state WHERE key=?', (key,)).fetchone()
        return row['value'] if row else default

    def _set(self, key: str, value: str) -> None:
        self.db.execute('INSERT INTO bot_state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, value))

    def _notify(self, text: str) -> None:
        if self.telegram and not self.telegram.send(text):
            event(self.log, 'notification_failed')

    def _status(self) -> str:
        balances = self.executor.balances()
        risk = self.db.execute('SELECT killed,daily_halted FROM risk_state WHERE id=1').fetchone()
        return (f"PAPER | cash IDR {balances['IDR']:.2f} | positions {len(balances['positions'])} | "
                f"paused {self._get('paused','0')} | stopped {self._get('stopping','0')} | "
                f"daily halted {risk['daily_halted']} | kill {risk['killed']}")

    def process_commands(self) -> None:
        if not self.telegram:
            return
        offset = int(self._get('telegram_offset', '0'))
        for update in self.telegram.updates(offset, self.config.runtime.telegram_poll_limit):
            update_id = update.get('update_id')
            if not isinstance(update_id, int) or update_id < offset:
                continue
            cmd = self.telegram.command(update)
            # Persist command and offset together. Unauthorized messages only advance offset.
            self.db.execute('BEGIN IMMEDIATE')
            try:
                if cmd == '/pause': self._set('paused', '1')
                if cmd == '/resume' and self._get('stopping', '0') != '1': self._set('paused', '0')
                if cmd == '/stop': self._set('stopping', '1')
                self._set('telegram_offset', str(update_id + 1))
                self.db.execute('COMMIT')
            except Exception:
                self.db.execute('ROLLBACK')
                raise
            offset = update_id + 1
            if cmd:
                event(self.log, 'command', command=cmd)
                self._notify(self._status())

    def _seen_entry(self, symbol: str, candle_ms: int) -> bool:
        # Persist before trying entry: a crash can skip an entry, never repeat it.
        result = self.db.execute('INSERT OR IGNORE INTO bot_seen VALUES (?,?)', (symbol, candle_ms))
        return result.rowcount == 0

    def _sell(self, symbol: str, position: dict, reason: str, quotes, now_ms: int) -> None:
        try:
            order = self.executor.place_order('exit:' + position['entry_id'], symbol, 'sell', position['amount'], None, quotes, now_ms)
            entry = self.db.execute('SELECT result FROM orders WHERE client_id=?', (position['entry_id'],)).fetchone()
            entry_fee = json.loads(entry['result'])['fee_tax'] if entry else None
            pnl = (order['price'] - position['entry_price']) * order['amount'] - order['fee_tax'] - entry_fee if entry_fee is not None else None
            event(self.log, 'exit_fill', symbol=symbol, reason=reason, amount=order['amount'], price=order['price'], pnl_idr=pnl)
            self._notify(f'PAPER exit {symbol}: {reason}; PnL IDR {pnl:.2f}' if pnl is not None else f'PAPER exit {symbol}: {reason}; PnL unavailable')
        except OrderRejected:
            event(self.log, 'exit_rejected', symbol=symbol, reason=reason)
            self._notify(f'PAPER critical: exit rejected for {symbol}; position remains open')

    def run_once(self, now_ms: int) -> bool:
        """One iteration. Return True only when stopped with no open position."""
        self._persist_stop_request()
        self.process_commands()
        quotes, frames = self.data.snapshot(now_ms)
        self._persist_stop_request()
        # Use the actual post-fetch time in production; tests can supply a deterministic clock.
        now_ms = max(now_ms, int(self.clock() * 1000))
        decision, equity = self.executor.assess_risk(quotes, now_ms)
        if decision.state.killed and self._get('kill_notified', '0') != '1':
            self._set('kill_notified', '1')
            event(self.log, 'kill_switch', equity_idr=equity)
            self._notify('PAPER critical: kill switch active; attempting liquidation. Manual review required.')
        today = datetime.fromtimestamp(now_ms / 1000, timezone.utc).date().isoformat()
        prior_day = self._get('summary_day')
        if prior_day and prior_day != today:
            # We report the last recorded equity, not a reconstructed midnight mark.
            self._notify(f"PAPER daily summary {prior_day}: last equity IDR {self._get('last_equity', 'unknown')}")
            event(self.log, 'daily_summary', day=prior_day, equity_idr=self._get('last_equity', 'unknown'))
        self._set('summary_day', today)
        self._set('last_equity', f'{equity:.2f}')
        if now_ms - int(self._get('heartbeat_ms', '0')) >= self.config.runtime.heartbeat_seconds * 1000:
            event(self.log, 'heartbeat', equity_idr=equity)
            self._notify(self._status())
            self._set('heartbeat_ms', str(now_ms))
        signals = {symbol: self.strategy.signals(frame).iloc[-1] for symbol, frame in frames.items()}
        for symbol, row in signals.items():
            candle = str(int(row.timestamp.timestamp() * 1000))
            key = 'last_signal:' + symbol
            if self._get(key) != candle:
                event(self.log, 'signal', symbol=symbol, candle_ms=int(candle), entry=bool(row['entry']), exit=bool(row['exit']))
                self._set(key, candle)
        positions = self.executor.balances()['positions']
        stopping = self._get('stopping', '0') == '1'
        for symbol, position in positions.items():
            q, row = quotes[symbol], signals[symbol]
            reason = ('kill' if decision.state.killed else 'stop_command' if stopping else
                      'stop_loss' if q.bid <= position['stop'] else 'take_profit' if q.bid >= position['target'] else
                      'signal' if bool(row['exit']) else None)
            if reason:
                self._sell(symbol, position, reason, quotes, now_ms)
        if decision.state.killed or stopping:
            return not self.executor.balances()['positions']
        if self._get('paused', '0') == '1' or not decision.allow_entry:
            return False
        interval_ms = self.executor.exchange.parse_timeframe(self.config.market.timeframe) * 1000
        for symbol, row in signals.items():
            self._persist_stop_request()
            if self._stop_requested:
                break
            if not bool(row['entry']) or symbol in self.executor.balances()['positions']:
                continue
            candle_ms = int(row.timestamp.timestamp() * 1000)
            # Only enter close to the next candle's open. Never replay stale signals.
            age_ms = now_ms - (candle_ms + interval_ms)
            if age_ms < 0 or age_ms > self.config.runtime.entry_window_seconds * 1000:
                continue
            if self._seen_entry(symbol, candle_ms):
                continue
            q = quotes[symbol]
            price = q.ask * (1 + self.config.execution.slippage_rate)
            stop = price - float(row.atr) * self.config.strategy.atr_stop_multiplier
            try:
                current_decision, current_equity = self.executor.assess_risk(quotes, now_ms)
                if not current_decision.allow_entry:
                    continue
                balance = self.executor.balances()['IDR']
                amount = position_size(current_equity, price, stop, self.config.risk.risk_per_trade,
                                       self.config.execution.taker_buy_fee_rate, self.config.execution.buy_tax_rate,
                                       self.config.execution.slippage_rate, balance,
                                       sell_fee_rate=self.config.execution.taker_sell_fee_rate,
                                       sell_tax_rate=self.config.execution.sell_tax_rate)
                amount = float(self.executor.exchange.amount_to_precision(symbol, amount))
                if self._stop_requested:
                    self._persist_stop_request()
                    break
                order = self.executor.place_order(f'entry:{symbol}:{candle_ms}', symbol, 'buy', amount, stop, quotes, now_ms)
                event(self.log, 'entry_fill', symbol=symbol, amount=amount, price=order['price'], stop=stop, fee_tax=order['fee_tax'])
                self._notify(f'PAPER entry {symbol}: amount {amount}, price {order["price"]:.2f}, stop {stop:.2f}')
            except (OrderRejected, ValueError):
                event(self.log, 'entry_rejected', symbol=symbol)
        return False

    def run(self) -> None:
        event(self.log, 'startup', mode='paper')
        self._notify('PAPER started; virtual funds only.')
        while True:
            try:
                if self.run_once(int(time.time() * 1000)):
                    event(self.log, 'stopped', reason='kill_or_stop')
                    self._notify('PAPER stopped after liquidation; manual restart/review required.')
                    return
            except Exception as exc:
                self._persist_stop_request()
                # Do not log raw exception text: ccxt/requests may embed secrets.
                event(self.log, 'iteration_error', error_type=type(exc).__name__)
                self._notify(f'PAPER error: {type(exc).__name__}; entries suspended until next successful cycle')
            time.sleep(self.config.runtime.poll_interval_seconds)
