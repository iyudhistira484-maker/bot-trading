"""Entry point for backtest and virtual paper trading; live is fail-closed."""
import argparse
import fcntl
import os
import signal
from pathlib import Path
import sys

import ccxt
from dotenv import load_dotenv

from src.config import load_config
from src.data.market import MarketData
from src.execution.live import LiveExecutor
from src.execution.paper import PaperExecutor
from src.notify.telegram import Telegram
from src.paper_bot import PaperBot
from src.utils.logging import create_logger, event


def parser_for_paper():
    parser = argparse.ArgumentParser(description='Virtual paper bot (no live orders)')
    parser.add_argument('--config', default='config/config.yaml')
    parser.add_argument('--reset-stop', action='store_true', help='clear a previous stop after manual review')
    parser.add_argument('--reset-kill', action='store_true', help='reset kill only with no open positions, after manual review')
    return parser


def confirm(phrase: str) -> bool:
    if not sys.stdin.isatty():
        return False
    try:
        return input(f'Type {phrase} to confirm: ').strip() == phrase
    except (EOFError, KeyboardInterrupt):
        return False


def reset_state(bot: PaperBot, reset_stop: bool, reset_kill: bool) -> None:
    if not reset_stop and not reset_kill:
        return
    if not confirm('RESET PAPER'):
        raise ValueError('Manual confirmation required')
    db = bot.db
    db.execute('BEGIN IMMEDIATE')
    try:
        if reset_kill:
            if db.execute('SELECT COUNT(*) AS n FROM positions').fetchone()['n']:
                raise ValueError('Cannot reset kill with an open position')
            cash = db.execute('SELECT cash FROM account WHERE id=1').fetchone()['cash']
            if cash <= 0:
                raise ValueError('Cannot reset kill with no equity')
            # A new peak is explicit and audited. Do not clear the daily-loss latch.
            db.execute('UPDATE risk_state SET killed=0,peak=? WHERE id=1', (cash,))
            bot._set('kill_notified', '0')
        if reset_stop:
            if db.execute('SELECT COUNT(*) AS n FROM positions').fetchone()['n']:
                raise ValueError('Cannot reset stop with an open position')
            bot._set('stopping', '0')
        db.execute('COMMIT')
    except Exception:
        db.execute('ROLLBACK')
        raise
    event(bot.log, 'manual_reset', reset_stop=reset_stop, reset_kill=reset_kill)


def run_paper(args) -> int:
    config = load_config(args.config)
    load_dotenv()
    logger = create_logger(config.runtime.log_file, config.runtime.log_max_bytes, config.runtime.log_backup_count)
    db_path = Path(config.runtime.paper_database)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(db_path) + '.lock', 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('Another paper process holds the database lock.', file=sys.stderr)
            return 1
        exchange = ccxt.indodax({'enableRateLimit': True, 'timeout': config.market.timeout_ms})
        data = MarketData(exchange, config)
        data.load_markets()
        executor = PaperExecutor(db_path, exchange, config)
        try:
            token, chat = os.getenv('TELEGRAM_BOT_TOKEN'), os.getenv('TELEGRAM_CHAT_ID')
            if bool(token) != bool(chat):
                raise ValueError('Set both Telegram variables or neither')
            telegram = Telegram(token, chat) if token and chat else None
            bot = PaperBot(executor, data, config, logger, telegram)
            reset_state(bot, args.reset_stop, args.reset_kill)
            previous_sigterm = signal.signal(signal.SIGTERM, lambda _signum, _frame: bot.request_stop())
            try:
                bot.run()
            except KeyboardInterrupt:
                event(logger, 'interrupted', open_positions=len(executor.balances()['positions']))
                bot._notify('PAPER interrupted: open positions, if any, are no longer monitored until restart.')
                print('Paper stopped. Open positions may be unmonitored; review SQLite before restarting.', file=sys.stderr)
                return 130
            finally:
                signal.signal(signal.SIGTERM, previous_sigterm)
        finally:
            executor.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        argv = ['paper']
    mode = argv.pop(0)
    if mode == 'backtest':
        from src.backtest.run import main as backtest
        backtest(argv)
        return 0
    if mode == 'paper':
        args = parser_for_paper().parse_args(argv)
        try:
            return run_paper(args)
        except Exception as exc:
            # Never print raw API or HTTP exception messages, which may contain secrets.
            print(f'Paper startup failed ({type(exc).__name__}); inspect configuration and connectivity.', file=sys.stderr)
            return 1
    if mode == 'live':
        parser = argparse.ArgumentParser(description='Live trading is currently disabled')
        parser.add_argument('--config', default='config/config.yaml')
        args = parser.parse_args(argv)
        config = load_config(args.config)
        if not config.execution.live_enabled:
            print('Live blocked: live_enabled is false.', file=sys.stderr)
            return 1
        if not confirm('LIVE'):
            print('Live blocked: interactive confirmation required.', file=sys.stderr)
            return 1
        try:
            LiveExecutor()
        except RuntimeError:
            print('Live blocked: safe exchange reconciliation and uncertain-order handling are not implemented.', file=sys.stderr)
            return 1
    print('Usage: python -m src.main [backtest|paper|live] [options]', file=sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(main())
