import fcntl
import http.client
import json
import logging
import os
from pathlib import Path
import threading
import time

import pytest
import yaml

from src.config import load_config
from src.dashboard.auth import Auth, write_password
from src.dashboard.server import DashboardServer
from src.dashboard.store import DashboardError, PaperStore, effective_config
from src.execution.paper import OrderRejected, PaperExecutor, Quote
from src.paper_bot import PaperBot


class Exchange:
    def parse_timeframe(self, _): return 3600
    def market(self, _): return {'spot': True, 'active': True, 'quote': 'IDR', 'limits': {'amount': {'min': .01}, 'cost': {'min': 5}}}
    def amount_to_precision(self, _, amount): return f'{int(amount * 100) / 100:.2f}'


@pytest.fixture
def setup(tmp_path):
    cfg = load_config()
    cfg.runtime.paper_database = str(tmp_path / 'paper.sqlite')
    cfg.runtime.log_file = str(tmp_path / 'paper.log')
    cfg.execution.min_notional_idr = {}
    config_path = tmp_path / 'config.yaml'
    config_path.write_text(yaml.safe_dump(cfg.model_dump()))
    ex = PaperExecutor(cfg.runtime.paper_database, Exchange(), cfg, initial_cash=10000)
    bot = PaperBot(ex, None, cfg, logging.Logger('test'))
    store = PaperStore(config_path)
    yield store, ex, bot, cfg
    ex.close()


def test_auth_password_permissions_sessions_and_rate_limit(tmp_path):
    path = tmp_path / 'auth.json'
    with pytest.raises(ValueError): write_password(path, 'short')
    write_password(path, 'a safe password here')
    assert path.stat().st_mode & 0o777 == 0o600
    assert 'a safe password here' not in path.read_text()
    with pytest.raises(FileExistsError): write_password(path, 'another safe password')
    auth = Auth(path)
    assert all(auth.login('bad', '127.0.0.1') is None for _ in range(5))
    assert auth.login('a safe password here', '127.0.0.1') is None
    token, csrf = auth.login('a safe password here', 'another-client')
    assert auth.session(token) == csrf
    auth.logout(token)
    assert auth.session(token) is None


def test_status_history_and_settings_persist(setup):
    store, ex, bot, cfg = setup
    now = int(time.time() * 1000)
    quote = Quote(99, 101, 100, now, now-1000, 100)
    ex.place_order('buy', 'BTC/IDR', 'buy', 1, 90, {'BTC/IDR': quote}, now)
    assert len(store.status()['positions']) == 1
    with pytest.raises(DashboardError): store.save_settings({k: store.settings()[k] for k in ('risk', 'strategy')})
    ex.place_order('sell', 'BTC/IDR', 'sell', 1, None, {'BTC/IDR': quote}, now)
    history = store.history()
    assert [x['side'] for x in history] == ['sell', 'buy']
    assert history[0]['pnl'] < 0 and history[1]['pnl'] is None
    changes = {k: store.settings()[k] for k in ('risk', 'strategy')}
    changes['risk']['risk_per_trade'] = .015
    store.save_settings(changes)
    assert effective_config(cfg, store.db_path).risk.risk_per_trade == .015
    assert PaperStore(store.config_path).settings()['risk']['risk_per_trade'] == .015
    changes['risk']['risk_per_trade'] = .03
    with pytest.raises(DashboardError): store.save_settings(changes)
    assert store.settings()['risk']['risk_per_trade'] == .015
    changes['risk']['risk_per_trade'] = .01
    changes['strategy']['ema_fast'] = 100
    with pytest.raises(DashboardError): store.save_settings(changes)
    changes['strategy']['ema_fast'] = 20
    changes['risk']['max_capital_idr'] = 99999999
    with pytest.raises(DashboardError): store.save_settings(changes)


def test_lock_kill_and_stop_reset(setup):
    store, ex, bot, _ = setup
    changes = {k: store.settings()[k] for k in ('risk', 'strategy')}
    with open(str(store.db_path) + '.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(DashboardError): store.save_settings(changes)
        with pytest.raises(DashboardError): store.control('reset_stop')
    store.control('stop'); assert bot._get('stopping') == '1'
    with pytest.raises(DashboardError): store.control('resume')
    ex.db.execute('UPDATE risk_state SET killed=1')
    with pytest.raises(DashboardError): store.control('reset_stop')
    with pytest.raises(DashboardError): store.save_settings(changes)
    ex.db.execute('UPDATE risk_state SET killed=0')
    store.control('reset_stop'); assert bot._get('stopping') == '0'


@pytest.mark.parametrize('action', ['pause', 'stop'])
def test_pause_and_stop_block_buy_but_not_sell(setup, action):
    store, ex, bot, _ = setup
    now = int(time.time() * 1000); q = {'BTC/IDR': Quote(99, 101, 100, now, now-1000, 100)}
    ex.place_order('first', 'BTC/IDR', 'buy', 1, 90, q, now)
    store.control(action)
    ex.place_order('sell', 'BTC/IDR', 'sell', 1, None, q, now)
    with pytest.raises(OrderRejected, match='Paused or stopping'):
        ex.place_order('second', 'BTC/IDR', 'buy', 1, 90, q, now)
    assert not ex.balances()['positions']
    if action == 'pause':
        store.control('resume')
        ex.place_order('third', 'BTC/IDR', 'buy', 1, 90, q, now)


def test_service_stop_does_not_replace_explicit_stop(setup):
    store, ex, bot, _ = setup
    store.control('stop')
    bot.request_stop(); bot._persist_stop_request()
    assert bot._get('stopping') == '1'


def test_corrupt_override_fails_closed(setup):
    store, ex, bot, cfg = setup
    ex.db.execute('CREATE TABLE paper_settings (id INTEGER PRIMARY KEY, value TEXT NOT NULL)')
    ex.db.execute('INSERT INTO paper_settings VALUES (1, ?)', ('{"risk":{"risk_per_trade":0.9}}',))
    with pytest.raises(ValueError): effective_config(cfg, store.db_path)


def test_http_access_csrf_origin_and_control(tmp_path, setup):
    store, ex, bot, cfg = setup
    password = 'strong paper password'
    path = tmp_path / 'auth.json'; write_password(path, password)
    server = DashboardServer(0, Auth(path), store)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    port = server.server_address[1]; host = f'127.0.0.1:{port}'
    def req(method, path, body=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse(); data = response.read(); result = (response.status, dict(response.getheaders()), data); conn.close(); return result
    try:
        assert server.server_address[0] == '127.0.0.1'
        assert req('GET', '/api/status')[0] == 401
        assert req('GET', '/', headers={'Host':'evil.example'})[0] == 400
        assert req('GET', '/static/app.js')[0] == 200
        assert req('GET', '/static/../auth.json')[0] == 401
        assert req('POST','/api/login',json.dumps({'password':password}),{'Content-Type':'application/json','Origin':'http://evil.example'})[0] == 403
        headers = {'Content-Type':'application/json','Origin':'http://' + host}
        status, h, _ = req('POST','/api/login',json.dumps({'password':password}),headers)
        assert status == 200 and 'HttpOnly' in h['Set-Cookie'] and 'SameSite=Strict' in h['Set-Cookie']
        cookie = h['Set-Cookie'].split(';',1)[0]
        status, _, data = req('GET','/api/session',headers={'Cookie':cookie})
        csrf = json.loads(data)['csrf']; assert status == 200
        payload = json.dumps({'action':'pause'})
        assert req('POST','/api/control',payload,headers | {'Cookie':cookie})[0] == 403
        assert bot._get('paused','0') == '0'
        authorized = headers | {'Cookie':cookie,'X-CSRF-Token':csrf}
        assert req('POST','/api/control',payload,authorized)[0] == 200
        assert bot._get('paused') == '1'
        assert req('POST','/api/logout','{}',authorized)[0] == 200
        assert req('GET','/api/status',headers={'Cookie':cookie})[0] == 401
    finally:
        server.shutdown(); thread.join(); server.server_close()


def test_concurrent_pause_commits_before_waiting_buy(setup, monkeypatch):
    import src.dashboard.store as store_module
    store, ex, bot, cfg = setup
    marked, release, buyer_ready = threading.Event(), threading.Event(), threading.Event()
    original = store_module._set
    def delayed_set(db, key, value):
        original(db, key, value)
        if key == 'paused':
            marked.set()
            assert release.wait(5)
    monkeypatch.setattr(store_module, '_set', delayed_set)
    results = []
    def pause():
        try: store.control('pause')
        except Exception as err: results.append(err)
    def buy():
        executor = PaperExecutor(store.db_path, Exchange(), cfg, initial_cash=10000)
        buyer_ready.set()
        try:
            assert marked.wait(5)
            now = int(time.time()*1000)
            q = {'BTC/IDR': Quote(99, 101, 100, now, now-1000, 100)}
            executor.place_order('simultaneous', 'BTC/IDR', 'buy', 1, 90, q, now)
            results.append('unexpected fill')
        except Exception as err: results.append(err)
        finally: executor.close()
    buyer = threading.Thread(target=buy); buyer.start(); assert buyer_ready.wait(5)
    pauser = threading.Thread(target=pause); pauser.start(); assert marked.wait(5)
    release.set(); buyer.join(8); pauser.join(8)
    assert not buyer.is_alive() and not pauser.is_alive()
    assert len(results) == 1 and isinstance(results[0], OrderRejected)
    assert not ex.balances()['positions']


def test_reset_stop_rejects_open_position(setup):
    store, ex, bot, _ = setup
    now = int(time.time()*1000); q = {'BTC/IDR': Quote(99,101,100,now,now-1000,100)}
    ex.place_order('open', 'BTC/IDR', 'buy', 1, 90, q, now)
    store.control('stop')
    with pytest.raises(DashboardError): store.control('reset_stop')
    assert bot._get('stopping') == '1'


def test_status_shows_timestamp_and_sanitizes_events(setup):
    store, ex, bot, _ = setup
    bot._set('last_equity', '9999.99')
    bot._set('last_equity_updated_at', '2026-11-23T04:00:00+00:00')
    Path(store.log_path).write_text(
        json.dumps({'event':'heartbeat', 'time':'2026-11-23T04:00:00+00:00', 'equity_idr':9999.99, 'secret':'hidden'}) + '\n'
        + json.dumps({'event':'unapproved', 'secret':'hidden'}) + '\n')
    status = store.status()
    assert status['equity'] == '9999.99'
    assert status['equity_updated_at'] == '2026-11-23T04:00:00+00:00'
    assert status['heartbeat']['equity_idr'] == 9999.99
    assert len(status['events']) == 1 and 'secret' not in status['events'][0]


def test_dashboard_unit_is_unprivileged_and_paper_only():
    from configparser import ConfigParser
    unit = ConfigParser(interpolation=None)
    unit.read('deploy/vps/indodax-dashboard.service')
    service = unit['Service']
    assert service['user'] == 'indodaxbot'
    assert service['group'] == 'indodaxbot'
    assert service['execstart'].endswith('-m src.dashboard serve')
    assert service['nonewprivileges'] == 'true'
