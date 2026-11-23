"""Independent forward signal observer. Public candles only; no orders or paper DB.

Initialize a new empty study directory once, then schedule `sample` periodically.
Each observation is a hypothetical signal filter decision, not a fill or PnL.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
from importlib.metadata import version

import ccxt
import pandas as pd
import yaml

from src.config import load_config
from src.data.ohlcv import fetch_history
from src.research.cost_atr import MAX_COST_TO_STOP, apply_cost_filter
from src.strategy.ema import EMATrendStrategy

# 5-minute sampling is recommended. Observations >15 minutes late are rejected.
MAX_DELAY_MS = 15 * 60 * 1000
SCHEMA = 1


def fingerprint():
    """Stop the study if indicator/filter code or numerical dependencies change."""
    root = Path(__file__).resolve().parents[2]
    paths = ['src/research/shadow.py', 'src/research/cost_atr.py', 'src/strategy/ema.py',
             'src/data/ohlcv.py', 'src/config.py']
    return {'files': {p: hashlib.sha256((root / p).read_bytes()).hexdigest() for p in paths},
            'packages': {p: version(p) for p in ('ccxt', 'pandas', 'numpy', 'ta', 'pydantic', 'PyYAML')}}


def now_ms():
    return time.time_ns() // 1_000_000


def initialize(directory: Path, config_path: Path, start_ms: int | None = None):
    # Directory must not exist: avoids overwriting prior studies or the paper DB.
    config = load_config(config_path)
    if config.execution.live_enabled:
        raise ValueError('Shadow requires live_enabled=false')
    if len(config.market.pairs) != 1 or config.market.timeframe != '1h':
        raise ValueError('This study supports one pair on 1h only')
    minimum = max(config.strategy.ema_trend + 2, config.strategy.rsi_period + 2, config.strategy.atr_period + 2)
    if config.market.ohlcv_limit < minimum:
        raise ValueError('OHLCV limit too small for warmup')
    directory.mkdir(mode=0o700, parents=False, exist_ok=False)
    # Only a frozen normalized config, no keys or environment variables.
    config_text = yaml.safe_dump(config.model_dump(), sort_keys=True)
    (directory / 'config.yaml').write_text(config_text, encoding='utf-8')
    os.chmod(directory / 'config.yaml', 0o600)
    metadata = {'schema': SCHEMA, 'started_ms': now_ms() if start_ms is None else start_ms,
                'config_sha256': hashlib.sha256(config_text.encode()).hexdigest(),
                'fingerprint': fingerprint(), 'threshold': MAX_COST_TO_STOP,
                'max_delay_ms': MAX_DELAY_MS}
    (directory / 'study.json').write_text(json.dumps(metadata, indent=2) + '\n', encoding='utf-8')
    os.chmod(directory / 'study.json', 0o600)
    with sqlite3.connect(directory / 'shadow.sqlite') as db:
        db.execute('''CREATE TABLE observations (
            candle_ms INTEGER PRIMARY KEY, observed_ms INTEGER NOT NULL,
            symbol TEXT NOT NULL, close REAL NOT NULL, atr REAL,
            entry INTEGER NOT NULL, exit INTEGER NOT NULL, filter_pass INTEGER,
            missed_candles INTEGER NOT NULL CHECK (missed_candles >= 0))''')
    os.chmod(directory / 'shadow.sqlite', 0o600)
    return metadata


def open_study(directory: Path):
    meta = json.loads((directory / 'study.json').read_text(encoding='utf-8'))
    config_bytes = (directory / 'config.yaml').read_bytes()
    if (meta['schema'] != SCHEMA or meta['threshold'] != MAX_COST_TO_STOP
            or meta['max_delay_ms'] != MAX_DELAY_MS
            or meta['config_sha256'] != hashlib.sha256(config_bytes).hexdigest()
            or meta['fingerprint'] != fingerprint()):
        raise ValueError('Study definition or environment changed; stop and review')
    config = load_config(directory / 'config.yaml')
    db_path = directory / 'shadow.sqlite'
    if not db_path.is_file():
        raise ValueError('Study database missing')
    # rw mode prevents accidental recreation of a missing study database.
    db = sqlite3.connect(db_path.resolve().as_uri() + '?mode=rw', uri=True, timeout=30)
    return meta, config, db


def sample(directory: Path, exchange_factory=None, clock=now_ms):
    meta, config, db = open_study(directory)
    try:
        interval = ccxt.Exchange.parse_timeframe(config.market.timeframe) * 1000
        started = clock()
        close_ms = (started // interval) * interval
        candle_ms = close_ms - interval
        if close_ms <= meta['started_ms']:
            return 'Waiting for first candle that closes after study start.'
        previous = db.execute('SELECT MAX(candle_ms) FROM observations').fetchone()[0]
        if previous is not None and candle_ms <= previous:
            return 'Already observed this candle; no request made.'
        if started - close_ms > MAX_DELAY_MS:
            raise ValueError('Current candle observation too late; no backfill')
        exchange = (exchange_factory or ccxt.indodax)(
            {'enableRateLimit': True, 'timeout': config.market.timeout_ms})
        # Same rolling window as paper, but independent fetch and no ticker/private calls.
        data = fetch_history(exchange, config.market.pairs[0], config.market.timeframe,
                             close_ms - config.market.ohlcv_limit * interval, started,
                             config.market.ohlcv_limit, config.market.retries,
                             config.market.retry_delay_seconds)
        observed = clock()
        if observed - close_ms > MAX_DELAY_MS or observed < close_ms:
            raise ValueError('Observation too late; no backfill')
        if int(data.timestamp.iloc[-1].timestamp() * 1000) != candle_ms or len(data) != config.market.ohlcv_limit:
            raise ValueError('Incomplete or stale candle window')
        signals = EMATrendStrategy(config.strategy).signals(data)
        filtered = apply_cost_filter(signals, config)
        row = signals.iloc[-1]
        entry = bool(row['entry'])
        atr = float(row.atr) if pd.notna(row.atr) else None
        passed = int(bool(filtered.iloc[-1]['entry'])) if entry else None
        first_close = ((meta['started_ms'] // interval) + 1) * interval
        expected = first_close - interval if previous is None else previous + interval
        missed = max(0, (candle_ms - expected) // interval)
        with db:
            inserted = db.execute('INSERT OR IGNORE INTO observations VALUES (?,?,?,?,?,?,?,?,?)',
                                  (candle_ms, observed, config.market.pairs[0], float(row['close']), atr,
                                   int(entry), int(bool(row['exit'])), passed, missed)).rowcount
        if not inserted:
            return 'Already observed this candle; duplicate ignored.'
        return f'Observed closed candle {pd.to_datetime(candle_ms, unit="ms", utc=True).isoformat()}; entry={entry}, filter_pass={passed}, missed_candles={missed}.'
    finally:
        db.close()


def status(directory: Path):
    meta, config, db = open_study(directory)
    try:
        total, entries, passed, missed = db.execute('''SELECT COUNT(*), COALESCE(SUM(entry),0),
            COALESCE(SUM(CASE WHEN filter_pass=1 THEN 1 ELSE 0 END),0),
            COALESCE(SUM(missed_candles),0) FROM observations''').fetchone()
        last = db.execute('SELECT candle_ms, observed_ms FROM observations ORDER BY candle_ms DESC LIMIT 1').fetchone()
        print('Start UTC:', pd.to_datetime(meta['started_ms'], unit='ms', utc=True).isoformat())
        print('Pair/timeframe:', config.market.pairs[0], config.market.timeframe)
        print('Observations:', total, '| Entry signals:', entries, '| Filter passed:', passed,
              '| Filter blocked:', entries - passed, '| Missed candles:', missed)
        if last:
            print('Last candle UTC:', pd.to_datetime(last[0], unit='ms', utc=True).isoformat())
            print('Recorded UTC:', pd.to_datetime(last[1], unit='ms', utc=True).isoformat())
        else:
            print('No observations yet.')
        interval = ccxt.Exchange.parse_timeframe(config.market.timeframe) * 1000
        latest_close = now_ms() // interval * interval
        first_close = (meta['started_ms'] // interval + 1) * interval
        pending_start = first_close if last is None else last[0] + 2 * interval
        pending = max(0, (latest_close - pending_start) // interval + 1)
        print('Closed candles not yet recorded since last observation/start:', pending,
              '(latest may still be awaiting scheduled sampling)')
        print('Signal decisions only: no trades, fills, positions, or PnL; may differ from active paper.')
    finally:
        db.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest='command', required=True)
    init = subs.add_parser('init')
    init.add_argument('--dir', type=Path, required=True)
    init.add_argument('--config', type=Path, required=True)
    for name in ('sample', 'status'):
        command = subs.add_parser(name)
        command.add_argument('--dir', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'init':
            metadata = initialize(args.dir, args.config)
            print('Study initialized. Start UTC:', pd.to_datetime(metadata['started_ms'], unit='ms', utc=True).isoformat())
            print('Only candles closing after start can be recorded.')
        elif args.command == 'sample':
            print(sample(args.dir))
        else:
            status(args.dir)
    except Exception as exc:
        # Network errors may contain URLs; avoid printing arbitrary exception text.
        print(f'Shadow {args.command} failed ({type(exc).__name__}); no orders sent. Review setup, clock, data and study integrity.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
