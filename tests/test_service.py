from configparser import ConfigParser
from pathlib import Path


def test_systemd_unit_is_paper_only_and_fail_closed():
    unit = ConfigParser(interpolation=None)
    unit.read(Path('deploy/vps/indodax-paper.service'))
    svc = unit['Service']
    assert svc['user'] == 'indodaxbot'
    assert svc['group'] == 'indodaxbot'
    assert svc['execstart'].endswith('-m src.main paper')
    assert svc['restart'] == 'on-failure'
    assert int(svc['timeoutstopsec']) >= 180
    assert svc['nonewprivileges'] == 'true'


def test_sigterm_persists_stop_and_exits_cleanly(tmp_path):
    import os
    import select
    import signal
    import sqlite3
    import subprocess
    import sys
    import time
    import yaml
    from src.config import load_config

    config = load_config().model_dump()
    config['runtime']['paper_database'] = str(tmp_path / 'paper.sqlite')
    config['runtime']['log_file'] = str(tmp_path / 'paper.log')
    config['runtime']['poll_interval_seconds'] = .05
    path = tmp_path / 'config.yaml'
    path.write_text(yaml.safe_dump(config))
    script = r'''
import sys
import pandas as pd
from src import main as m
from src.execution.paper import Quote
class Exchange:
    def parse_timeframe(self, timeframe): return 3600
class Feed:
    def __init__(self, exchange, config): pass
    def load_markets(self): pass
    def snapshot(self, now):
        close = now // 3600000 * 3600000
        q = Quote(99, 101, 100, now, close, 100)
        df = pd.DataFrame({'timestamp':[pd.to_datetime(close - 3600000, unit='ms', utc=True)],
                           'open':[100.], 'high':[101.], 'low':[99.], 'close':[100.], 'volume':[1.]})
        print('READY', flush=True)
        return {'BTC/IDR':q}, {'BTC/IDR':df}
m.ccxt.indodax = lambda config: Exchange()
m.MarketData = Feed
m.load_dotenv = lambda: False
sys.exit(m.main(['paper', '--config', sys.argv[1]]))
'''
    env = os.environ.copy()
    env.pop('TELEGRAM_BOT_TOKEN', None); env.pop('TELEGRAM_CHAT_ID', None)
    proc = subprocess.Popen([sys.executable, '-c', script, str(path)], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, env=env)
    try:
        deadline = time.monotonic() + 15
        output = b''
        while b'READY\n' not in output and time.monotonic() < deadline:
            ready, _, _ = select.select([proc.stdout], [], [], max(0, deadline - time.monotonic()))
            if not ready:
                break
            chunk = os.read(proc.stdout.fileno(), 4096)
            if not chunk:
                break
            output += chunk
        assert b'READY\n' in output, 'paper subprocess did not start'
        proc.send_signal(signal.SIGTERM)
        _, stderr = proc.communicate(timeout=15)
        assert proc.returncode == 0, stderr
        with sqlite3.connect(config['runtime']['paper_database']) as db:
            assert db.execute("SELECT value FROM bot_state WHERE key='stopping'").fetchone()[0] == '1'
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()
