"""Local paper account access; no private API or live order capabilities."""
from __future__ import annotations

from collections import deque
from contextlib import closing, contextmanager
import fcntl
import json
from pathlib import Path
import sqlite3
import subprocess
from urllib.parse import quote

from src.config import Config, load_config

STRATEGY_FIELDS = {'ema_fast', 'ema_slow', 'ema_trend', 'rsi_period', 'rsi_min', 'rsi_max',
                   'atr_period', 'atr_min', 'atr_stop_multiplier', 'risk_reward'}
RISK_FIELDS = {'risk_per_trade', 'daily_loss_limit', 'max_drawdown'}


class DashboardError(ValueError):
    """Safe, user-facing request failure."""


def db_connect(path: str | Path, *, read_only: bool = False) -> sqlite3.Connection:
    path = Path(path).resolve()
    if not path.is_file():
        raise DashboardError('Database paper belum tersedia. Jalankan bot paper dulu.')
    connection = sqlite3.connect(f'file:{quote(str(path))}?mode={"ro" if read_only else "rw"}',
                                 uri=True, timeout=5, isolation_level=None)
    connection.row_factory = sqlite3.Row
    return connection


def effective_config(base: Config, db_path: str | Path) -> Config:
    """Apply only explicitly allowed dashboard overrides; reject corrupt state."""
    if not Path(db_path).exists():
        return base
    with closing(db_connect(db_path, read_only=True)) as db:
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='paper_settings'").fetchone()
        row = db.execute('SELECT value FROM paper_settings WHERE id=1').fetchone() if exists else None
    if row is None:
        return base
    changes = json.loads(row['value'])
    if not isinstance(changes, dict) or set(changes) != {'strategy', 'risk'}:
        raise ValueError('Invalid saved paper settings')
    if (not isinstance(changes['strategy'], dict) or not isinstance(changes['risk'], dict)
            or set(changes['strategy']) != STRATEGY_FIELDS or set(changes['risk']) != RISK_FIELDS):
        raise ValueError('Invalid saved paper settings fields')
    values = base.model_dump()
    values['strategy'].update(changes['strategy'])
    values['risk'].update(changes['risk'])
    return Config.model_validate(values)


@contextmanager
def bot_stopped_lock(db_path: str | Path):
    """Share the bot's lock; never edit configuration while it runs."""
    lock_path = Path(str(db_path) + '.lock')
    if not lock_path.parent.is_dir():
        raise DashboardError('Database paper belum tersedia.')
    with open(lock_path, 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise DashboardError('Hentikan bot dan tunggu hingga selesai sebelum mengubah pengaturan.') from exc
        yield


def _set(db: sqlite3.Connection, key: str, value: str) -> None:
    db.execute('INSERT INTO bot_state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, value))


class PaperStore:
    def __init__(self, config_path: str | Path):
        self.config_path = Path(config_path)
        self.base = load_config(config_path)
        self.db_path = self.base.runtime.paper_database
        self.log_path = self.base.runtime.log_file

    def current_config(self) -> Config:
        return effective_config(load_config(self.config_path), self.db_path)

    def status(self) -> dict:
        with closing(db_connect(self.db_path, read_only=True)) as db:
            db.execute('BEGIN')
            account = db.execute('SELECT cash FROM account WHERE id=1').fetchone()
            risk = db.execute('SELECT day,day_start,peak,killed,daily_halted FROM risk_state WHERE id=1').fetchone()
            if not account or not risk:
                raise DashboardError('State paper tidak lengkap; perlu pemeriksaan manual.')
            positions = [dict(row) for row in db.execute('SELECT symbol,amount,entry_price,stop,target FROM positions ORDER BY symbol')]
            state = {r['key']: r['value'] for r in db.execute('SELECT key,value FROM bot_state')}
            count = db.execute('SELECT COUNT(*) FROM orders').fetchone()[0]
            db.execute('COMMIT')
        service = 'unknown'
        try:
            output = subprocess.run(['systemctl', 'is-active', 'indodax-paper.service'], capture_output=True, text=True, timeout=3, check=False)
            candidate = output.stdout.strip()
            if candidate in {'active', 'inactive', 'activating', 'deactivating', 'failed'}:
                service = candidate
        except (OSError, subprocess.TimeoutExpired):
            pass
        events = self.events()
        heartbeat = next((e for e in reversed(events) if e.get('event') == 'heartbeat'), None)
        return {'service': service, 'cash': account['cash'], 'equity': state.get('last_equity'),
                'equity_updated_at': state.get('last_equity_updated_at'), 'positions': positions, 'orders': count,
                'paused': state.get('paused', '0') == '1', 'stopping': state.get('stopping', '0'),
                'killed': bool(risk['killed']), 'daily_halted': bool(risk['daily_halted']),
                'day_start': risk['day_start'], 'peak': risk['peak'], 'heartbeat': heartbeat,
                'events': events[-12:]}

    def events(self) -> list[dict]:
        """Read bounded sanitized structured logs; never include raw exception strings."""
        allowed = {'startup', 'heartbeat', 'signal', 'entry_fill', 'exit_fill', 'kill_switch',
                   'exit_rejected', 'entry_rejected', 'iteration_error', 'notification_failed',
                   'daily_summary', 'stop_requested', 'service_stop_completed', 'stopped'}
        keys = {'time', 'event', 'symbol', 'entry', 'exit', 'amount', 'price', 'pnl_idr', 'equity_idr', 'reason', 'error_type'}
        try:
            with open(self.log_path, encoding='utf-8') as source:
                lines = deque(source, maxlen=150)
        except FileNotFoundError:
            return []
        output = []
        for line in lines:
            if len(line) > 10000:
                continue
            try:
                event = json.loads(line)
                if isinstance(event, dict) and event.get('event') in allowed:
                    output.append({k: event[k] for k in keys if k in event and isinstance(event[k], (str, bool, int, float, type(None)))})
            except (ValueError, TypeError):
                continue
        return output

    def history(self) -> list[dict]:
        with closing(db_connect(self.db_path, read_only=True)) as db:
            rows = db.execute('SELECT result FROM orders ORDER BY rowid').fetchall()
        buys: dict[str, dict] = {}
        result = []
        for row in rows:
            item = json.loads(row['result'])
            if not isinstance(item, dict) or item.get('side') not in ('buy', 'sell'):
                continue
            symbol = item['symbol']
            entry = buys.get(symbol)
            pnl = None
            if item['side'] == 'buy':
                buys[symbol] = item
            elif entry and entry['amount'] == item['amount']:
                pnl = (item['price'] - entry['price']) * item['amount'] - item['fee_tax'] - entry['fee_tax']
                buys.pop(symbol, None)
            result.append({key: item.get(key) for key in ('symbol', 'side', 'amount', 'price', 'fee_tax', 'timestamp_ms')} | {'pnl': pnl})
        return list(reversed(result[-100:]))

    def control(self, action: str) -> None:
        if action not in {'pause', 'resume', 'stop', 'reset_stop'}:
            raise DashboardError('Perintah tidak dikenal.')
        if action == 'reset_stop':
            with bot_stopped_lock(self.db_path), closing(db_connect(self.db_path)) as db:
                db.execute('BEGIN IMMEDIATE')
                try:
                    risk = db.execute('SELECT killed FROM risk_state WHERE id=1').fetchone()
                    count = db.execute('SELECT COUNT(*) FROM positions').fetchone()[0]
                    if not risk or risk['killed'] or count:
                        raise DashboardError('Reset ditolak: kill aktif atau posisi virtual masih terbuka.')
                    _set(db, 'stopping', '0')
                    db.execute('COMMIT')
                except Exception:
                    db.execute('ROLLBACK')
                    raise
            return
        with closing(db_connect(self.db_path)) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                risk = db.execute('SELECT killed,daily_halted FROM risk_state WHERE id=1').fetchone()
                stopping = db.execute("SELECT value FROM bot_state WHERE key='stopping'").fetchone()
                if not risk:
                    raise DashboardError('State risiko tidak tersedia.')
                if action == 'resume' and (risk['killed'] or risk['daily_halted'] or (stopping and stopping['value'] != '0')):
                    raise DashboardError('Resume ditolak: batas risiko atau stop aktif.')
                if action == 'pause': _set(db, 'paused', '1')
                if action == 'resume': _set(db, 'paused', '0')
                if action == 'stop': _set(db, 'stopping', '1')
                db.execute('COMMIT')
            except Exception:
                db.execute('ROLLBACK')
                raise

    def settings(self) -> dict:
        cfg = self.current_config()
        return {'strategy': {k: getattr(cfg.strategy, k) for k in sorted(STRATEGY_FIELDS)},
                'risk': {k: getattr(cfg.risk, k) for k in sorted(RISK_FIELDS)},
                'pair': cfg.market.pairs, 'timeframe': cfg.market.timeframe,
                'costs': {'buy': cfg.execution.taker_buy_fee_rate + cfg.execution.buy_tax_rate,
                          'sell': cfg.execution.taker_sell_fee_rate + cfg.execution.sell_tax_rate}}

    def save_settings(self, changes: dict) -> None:
        if (not isinstance(changes, dict) or set(changes) != {'strategy', 'risk'}
                or not isinstance(changes['strategy'], dict) or not isinstance(changes['risk'], dict)
                or set(changes['strategy']) != STRATEGY_FIELDS or set(changes['risk']) != RISK_FIELDS):
            raise DashboardError('Pengaturan tidak lengkap.')
        try:
            values = load_config(self.config_path).model_dump()
            values['strategy'].update(changes['strategy'])
            values['risk'].update(changes['risk'])
            Config.model_validate(values)
            encoded = json.dumps(changes, allow_nan=False, separators=(',', ':'))
        except (ValueError, TypeError) as exc:
            raise DashboardError('Nilai tidak valid. Risiko per trade maksimal 2%; periksa urutan EMA dan nilai lainnya.') from exc
        with bot_stopped_lock(self.db_path), closing(db_connect(self.db_path)) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                risk = db.execute('SELECT killed FROM risk_state WHERE id=1').fetchone()
                positions = db.execute('SELECT COUNT(*) FROM positions').fetchone()[0]
                if not risk or risk['killed'] or positions:
                    raise DashboardError('Pengaturan ditolak: kill aktif atau posisi virtual masih terbuka.')
                db.execute('CREATE TABLE IF NOT EXISTS paper_settings (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL)')
                db.execute('INSERT INTO paper_settings VALUES(1,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value', (encoded,))
                db.execute('COMMIT')
            except Exception:
                db.execute('ROLLBACK')
                raise
