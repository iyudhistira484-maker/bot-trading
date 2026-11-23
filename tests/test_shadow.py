import json
from pathlib import Path
import sqlite3

import pandas as pd
import pytest
import yaml

from src.research import shadow

INTERVAL = 3_600_000


@pytest.fixture
def study(tmp_path, config):
    config.market.ohlcv_limit = 210
    path = tmp_path / 'config.yaml'
    path.write_text(yaml.safe_dump(config.model_dump()))
    directory = tmp_path / 'study'
    shadow.initialize(directory, path, start_ms=INTERVAL * 1000 + 1000)
    return directory, path


def fake_history(exchange, symbol, timeframe, since, until, limit, retries, delay):
    times = pd.to_datetime([since + INTERVAL * i for i in range(limit)], unit='ms', utc=True)
    return pd.DataFrame({'timestamp': times, 'open': 100., 'high': 101., 'low': 99.,
                         'close': 100., 'volume': 1.})


def test_init_is_exclusive_and_snapshots_config(study):
    directory, path = study
    with pytest.raises(FileExistsError):
        shadow.initialize(directory, path)
    assert (directory / 'config.yaml').stat().st_mode & 0o777 == 0o600
    assert (directory / 'shadow.sqlite').stat().st_mode & 0o777 == 0o600
    assert shadow.open_study(directory)[1].market.pairs == ['BTC/IDR']


def test_sample_is_prospective_deduplicated_and_tracks_missed(study, monkeypatch):
    directory, _ = study
    calls = []
    def fetch(*args):
        calls.append(args)
        return fake_history(*args)
    monkeypatch.setattr(shadow, 'fetch_history', fetch)
    factory = lambda opts: object()
    assert 'Waiting' in shadow.sample(directory, factory, lambda: 1000*INTERVAL + 2000)
    assert not calls
    assert 'entry=False' in shadow.sample(directory, factory, lambda: 1001*INTERVAL + 2000)
    assert len(calls) == 1
    assert 'Already observed' in shadow.sample(directory, factory, lambda: 1001*INTERVAL + 5000)
    assert len(calls) == 1
    assert 'missed_candles=2' in shadow.sample(directory, factory, lambda: 1004*INTERVAL + 2000)
    with sqlite3.connect(directory / 'shadow.sqlite') as db:
        assert db.execute('SELECT entry, filter_pass, missed_candles FROM observations ORDER BY candle_ms').fetchall() == [(0, None, 0), (0, None, 2)]


def test_late_and_missing_data_are_not_recorded(study, monkeypatch):
    directory, _ = study
    monkeypatch.setattr(shadow, 'fetch_history', fake_history)
    with pytest.raises(ValueError, match='too late'):
        shadow.sample(directory, lambda _: object(), lambda: 1001*INTERVAL + shadow.MAX_DELAY_MS + 1)
    monkeypatch.setattr(shadow, 'fetch_history', lambda *args: fake_history(*args).iloc[:-1])
    with pytest.raises(ValueError, match='Incomplete'):
        shadow.sample(directory, lambda _: object(), lambda: 1001*INTERVAL + 2000)
    with sqlite3.connect(directory / 'shadow.sqlite') as db:
        assert db.execute('SELECT count(*) FROM observations').fetchone()[0] == 0


def test_first_sample_reports_startup_gap_and_slow_fetch_fails(study, monkeypatch):
    directory, _ = study
    monkeypatch.setattr(shadow, 'fetch_history', fake_history)
    ticks = iter([1003*INTERVAL + 2000, 1003*INTERVAL + shadow.MAX_DELAY_MS + 1])
    with pytest.raises(ValueError, match='too late'):
        shadow.sample(directory, lambda _: object(), lambda: next(ticks))
    assert 'missed_candles=2' in shadow.sample(directory, lambda _: object(), lambda: 1003*INTERVAL + 2000)


def test_changed_study_or_dependencies_rejected(study, monkeypatch):
    directory, _ = study
    config_path = directory / 'config.yaml'
    original = config_path.read_text()
    config_path.write_text(original + '\n# changed\n')
    with pytest.raises(ValueError, match='changed'): shadow.open_study(directory)
    config_path.write_text(original)
    monkeypatch.setattr(shadow, 'fingerprint', lambda: {})
    with pytest.raises(ValueError, match='changed'): shadow.open_study(directory)


def test_status_and_missing_database(study, capsys):
    directory, _ = study
    shadow.status(directory)
    assert 'No observations yet' in capsys.readouterr().out
    (directory / 'shadow.sqlite').unlink()
    with pytest.raises(ValueError, match='missing'): shadow.open_study(directory)
    assert not (directory / 'shadow.sqlite').exists()


def test_entry_filter_records_pass_or_block(study, monkeypatch):
    directory, _ = study
    monkeypatch.setattr(shadow, 'fetch_history', fake_history)
    class Strategy:
        def __init__(self, _): pass
        def signals(self, data):
            data = data.copy()
            data['atr'] = .0001
            data['entry'] = True
            data['exit'] = False
            return data
    monkeypatch.setattr(shadow, 'EMATrendStrategy', Strategy)
    shadow.sample(directory, lambda _: object(), lambda: 1001*INTERVAL + 1000)
    with sqlite3.connect(directory / 'shadow.sqlite') as db:
        assert db.execute('SELECT entry, filter_pass FROM observations').fetchone() == (1, 0)


def test_status_includes_unrecorded_ended_candles(study, monkeypatch, capsys):
    directory, _ = study
    monkeypatch.setattr(shadow, 'now_ms', lambda: 1003*INTERVAL + 2000)
    shadow.status(directory)
    assert 'start: 3 ' in capsys.readouterr().out
    monkeypatch.setattr(shadow, 'fetch_history', fake_history)
    shadow.sample(directory, lambda _: object(), lambda: 1003*INTERVAL + 2000)
    shadow.status(directory)
    output = capsys.readouterr().out
    assert 'Missed candles: 2' in output
    assert 'start: 0 ' in output
