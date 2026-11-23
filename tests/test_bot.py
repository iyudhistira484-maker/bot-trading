import logging
from datetime import datetime, timezone

import pandas as pd
import pytest
import yaml

from src.config import load_config
from src.execution.paper import PaperExecutor, Quote
from src.main import main, reset_state
from src.paper_bot import PaperBot


class Exchange:
    def parse_timeframe(self, _): return 3600
    def market(self, _):
        return {'spot': True, 'active': True, 'quote': 'IDR', 'limits': {'amount': {'min': .01}, 'cost': {'min': 5}}}
    def amount_to_precision(self, symbol, amount): return f'{int(amount * 100) / 100:.2f}'


class Data:
    def __init__(self, now):
        self.now = now
        self.close = now // 3600000 * 3600000
        self.q = Quote(99, 101, 100, now, self.close, 100)
        self.entry, self.exit = True, False
    def snapshot(self, now):
        df = pd.DataFrame({'timestamp': [pd.to_datetime(self.close - 3600000, unit='ms', utc=True)],
                           'entry': [self.entry], 'exit': [self.exit], 'atr': [5.]})
        return {'BTC/IDR': self.q}, {'BTC/IDR': df}


class Strategy:
    def signals(self, frame): return frame


class FakeTelegram:
    def __init__(self): self.queue = []; self.sent = []
    def updates(self, offset, limit): return [x for x in self.queue if x['update_id'] >= offset][:limit]
    def command(self, u): return u.get('cmd') if u.get('authorized', True) else None
    def send(self, text): self.sent.append(text); return True


@pytest.fixture
def context(tmp_path):
    cfg = load_config()
    cfg.execution.min_notional_idr = {}  # synthetic market has its own minimum
    cfg.runtime.entry_window_seconds = 60
    now = int(datetime.now(timezone.utc).timestamp() * 1000)
    # A recent close within the entry window; quote date must match database initialization.
    now = now // 3600000 * 3600000 + 1000
    exe = PaperExecutor(tmp_path / 'paper.db', Exchange(), cfg, initial_cash=10000)
    data = Data(now); tg = FakeTelegram()
    bot = PaperBot(exe, data, cfg, logging.Logger('test'), tg, clock=lambda: now / 1000)
    bot.strategy = Strategy()
    yield bot, data, tg, now
    exe.close()


def test_entry_once_then_stop_liquidates(context):
    bot, data, tg, now = context
    assert bot.run_once(now) is False
    assert 'BTC/IDR' in bot.executor.balances()['positions']
    bot.run_once(now + 1)
    assert bot.db.execute('SELECT COUNT(*) AS n FROM orders').fetchone()['n'] == 1
    tg.queue.append({'update_id': 1, 'cmd': '/stop'})
    assert bot.run_once(now + 2) is True
    assert bot.executor.balances()['positions'] == {}
    assert bot._get('stopping') == '1'
    assert bot.db.execute('SELECT COUNT(*) AS n FROM orders').fetchone()['n'] == 2


def test_unauthorized_pause_resume_and_stop_persist(context):
    bot, data, tg, now = context
    tg.queue = [{'update_id': 1, 'cmd': '/pause', 'authorized': False}, {'update_id': 2, 'cmd': '/pause'}]
    bot.run_once(now)
    assert bot._get('paused') == '1' and not bot.executor.balances()['positions']
    assert bot._get('telegram_offset') == '3'
    tg.queue.append({'update_id': 3, 'cmd': '/resume'})
    bot.run_once(now + 1)
    assert bot.executor.balances()['positions']


def test_stop_loss_executes_even_when_paused(context):
    bot, data, tg, now = context
    bot.run_once(now)
    position = bot.executor.balances()['positions']['BTC/IDR']
    bot._set('paused', '1')
    price = position['stop'] - 1
    data.q = Quote(price, price * 1.001, price, now, data.close, price)
    bot.run_once(now + 1)
    assert not bot.executor.balances()['positions']


def test_kill_closes_and_stops_sticky(context):
    bot, data, tg, now = context
    bot.run_once(now)
    bot.db.execute('UPDATE risk_state SET peak=20000')
    assert bot.run_once(now + 1)
    assert bot.db.execute('SELECT killed FROM risk_state').fetchone()['killed'] == 1
    assert not bot.executor.balances()['positions']
    assert any('kill switch active' in text for text in tg.sent)


def test_expired_signal_not_entered_and_no_reentry_after_exit(context):
    bot, data, tg, now = context
    bot.run_once(now + 65000)
    assert not bot.executor.balances()['positions']
    bot.run_once(now)
    position = bot.executor.balances()['positions']['BTC/IDR']
    data.q = Quote(position['target'] + 1, position['target'] + 1.01, position['target'] + 1, now, data.close, position['target'] + 1)
    bot.run_once(now + 1)
    assert not bot.executor.balances()['positions']
    bot.run_once(now + 2)
    assert not bot.executor.balances()['positions']


def test_manual_reset_kill_requires_confirmation_and_flat_position(context, monkeypatch):
    bot, data, tg, now = context
    bot.run_once(now)
    bot.db.execute('UPDATE risk_state SET killed=1')
    monkeypatch.setattr('src.main.confirm', lambda phrase: True)
    with pytest.raises(ValueError): reset_state(bot, False, True)
    assert bot.db.execute('SELECT killed FROM risk_state').fetchone()['killed'] == 1
    bot.run_once(now + 1)
    reset_state(bot, False, True)
    assert bot.db.execute('SELECT killed FROM risk_state').fetchone()['killed'] == 0


def test_live_blocked_even_with_config_and_confirmation(tmp_path, monkeypatch, capsys):
    cfg = load_config().model_dump()
    cfg['execution']['live_enabled'] = True
    path = tmp_path / 'config.yaml'; path.write_text(yaml.safe_dump(cfg))
    monkeypatch.setattr('src.main.confirm', lambda _: True)
    assert main(['live', '--config', str(path)]) == 1
    assert 'not implemented' in capsys.readouterr().err


def test_no_secret_leaks_on_startup_failure(monkeypatch, capsys):
    monkeypatch.setattr('src.main.run_paper', lambda args: (_ for _ in ()).throw(RuntimeError('secret-key')))
    assert main(['paper']) == 1
    assert 'secret-key' not in capsys.readouterr().err


def test_gap_can_trigger_stop_even_if_far_from_last_candle(context):
    bot, data, tg, now = context
    bot.run_once(now)
    data.q = Quote(70, 70.01, 70, now, data.close, 100)
    bot.run_once(now + 1)
    assert not bot.executor.balances()['positions']


def test_kill_rejected_exit_keeps_monitoring(context):
    bot, data, tg, now = context
    bot.run_once(now)
    bot.db.execute('UPDATE risk_state SET peak=20000')
    original = bot.executor.exchange.market
    def too_high(symbol):
        info = original(symbol)
        info['limits']['amount']['min'] = 10000
        return info
    bot.executor.exchange.market = too_high
    assert bot.run_once(now + 1) is False
    assert bot.executor.balances()['positions']
    assert any('exit rejected' in text for text in tg.sent)


def test_snapshot_failure_blocks_entry_without_balance_changes(context):
    bot, data, tg, now = context
    before = bot.executor.balances()
    def fail(_): raise TimeoutError('API timeout')
    data.snapshot = fail
    with pytest.raises(TimeoutError): bot.run_once(now)
    assert bot.executor.balances() == before
    assert bot.db.execute('SELECT COUNT(*) AS n FROM orders').fetchone()['n'] == 0


def test_default_mode_is_paper(monkeypatch):
    calls = []
    monkeypatch.setattr('src.main.run_paper', lambda args: calls.append(args) or 0)
    assert main([]) == 0
    assert len(calls) == 1


def test_service_stop_request_liquidates_and_is_sticky(context):
    bot, data, tg, now = context
    bot.run_once(now)
    assert bot.executor.balances()['positions']
    bot.request_stop()
    assert bot.run_once(now + 1) is True
    assert not bot.executor.balances()['positions']
    assert bot._get('stopping') == '0'
    assert bot.db.execute('SELECT COUNT(*) AS n FROM orders').fetchone()['n'] == 2


def test_stop_request_persists_even_if_data_unavailable(context):
    bot, data, tg, now = context
    bot.run_once(now)
    bot.request_stop()
    data.snapshot = lambda _: (_ for _ in ()).throw(TimeoutError())
    with pytest.raises(TimeoutError):
        bot.run_once(now + 1)
    assert bot._get('stopping') == 'service'
    assert bot.executor.balances()['positions']


def test_stop_request_during_data_fetch_prevents_entry(context):
    bot, data, tg, now = context
    original = data.snapshot
    def snapshot_with_stop(ts):
        bot.request_stop()
        return original(ts)
    data.snapshot = snapshot_with_stop
    assert bot.run_once(now) is True
    assert bot._get('stopping') == '0'
    assert not bot.executor.balances()['positions']


def test_incomplete_service_stop_remains_latched_until_liquidation(context):
    bot, data, tg, now = context
    bot.run_once(now)
    bot.request_stop()
    original = data.snapshot
    data.snapshot = lambda _: (_ for _ in ()).throw(TimeoutError())
    with pytest.raises(TimeoutError):
        bot.run_once(now + 1)
    assert bot._get('stopping') == 'service'
    bot._stop_requested = False  # simulate restart from the persisted database
    data.snapshot = original
    assert bot.run_once(now + 2) is True
    assert not bot.executor.balances()['positions']
    assert bot._get('stopping') == '0'


def test_kill_remains_latched_when_service_stop_completes(context):
    bot, data, tg, now = context
    bot.run_once(now)
    bot.db.execute('UPDATE risk_state SET peak=20000')
    bot.request_stop()
    assert bot.run_once(now + 1) is True
    assert bot._get('stopping') == 'service'
    assert bot.db.execute('SELECT killed FROM risk_state').fetchone()['killed'] == 1


def test_telegram_stop_is_not_cleared_by_service_shutdown(context):
    bot, data, tg, now = context
    tg.queue.append({'update_id': 1, 'cmd': '/stop'})
    bot.request_stop()
    assert bot.run_once(now) is True
    assert bot._get('stopping') == '1'


def test_reset_only_requires_an_explicit_reset():
    with pytest.raises(SystemExit) as exc:
        main(['paper', '--reset-only'])
    assert exc.value.code == 2


def test_reset_only_clears_stop_without_starting_loop(tmp_path, monkeypatch, capsys):
    from src import main as app
    from src.execution.paper import PaperExecutor
    cfg = load_config()
    cfg.runtime.paper_database = str(tmp_path / 'paper.sqlite')
    cfg.runtime.log_file = str(tmp_path / 'log.jsonl')
    exe = PaperExecutor(cfg.runtime.paper_database, Exchange(), cfg, initial_cash=10000)
    old_bot = PaperBot(exe, Data(int(datetime.now(timezone.utc).timestamp() * 1000)), cfg, logging.Logger('test'))
    old_bot._set('stopping', '1')
    exe.close()
    class Feed:
        def __init__(self, exchange, config): pass
        def load_markets(self): pass
    monkeypatch.setattr(app, 'load_config', lambda _: cfg)
    monkeypatch.setattr(app, 'load_dotenv', lambda: False)
    monkeypatch.setattr(app, 'MarketData', Feed)
    monkeypatch.setattr(app.ccxt, 'indodax', lambda _: Exchange())
    monkeypatch.setattr(app, 'confirm', lambda _: True)
    monkeypatch.delenv('TELEGRAM_BOT_TOKEN', raising=False)
    monkeypatch.delenv('TELEGRAM_CHAT_ID', raising=False)
    monkeypatch.setattr(app.PaperBot, 'run', lambda self: (_ for _ in ()).throw(AssertionError('must not start')))
    assert app.main(['paper', '--reset-stop', '--reset-only']) == 0
    assert 'bot was not started' in capsys.readouterr().out
    import sqlite3
    with sqlite3.connect(cfg.runtime.paper_database) as db:
        assert db.execute("SELECT value FROM bot_state WHERE key='stopping'").fetchone()[0] == '0'
