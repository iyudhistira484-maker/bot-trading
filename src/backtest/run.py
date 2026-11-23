"""Stage-2 backtest runner: python -m src.backtest.run --csv FILE or --since DATE."""
import argparse
from datetime import datetime, timezone

import ccxt
import pandas as pd

from src.backtest.engine import run_split
from src.backtest.history import fetch_indodax_history
from src.backtest.report import write_report
from src.config import load_config
from src.data.ohlcv import load_csv, closed_candles, save_csv
from src.strategy.ema import EMATrendStrategy


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--csv', help='OHLCV CSV with UTC timestamp and open/high/low/close/volume')
    source.add_argument('--since', help='UTC start date (ISO 8601) for Indodax public OHLCV')
    parser.add_argument('--config', default='config/config.yaml')
    parser.add_argument('--pair', help='Single configured pair; defaults to first pair')
    parser.add_argument('--output', default='reports/backtest')
    args = parser.parse_args(argv)
    config = load_config(args.config)
    pair = args.pair or config.market.pairs[0]
    if pair not in config.market.pairs:
        parser.error('Pair must be configured in market.pairs')
    ex = ccxt.indodax({'enableRateLimit': True, 'timeout': config.market.timeout_ms})
    interval_ms = ex.parse_timeframe(config.market.timeframe) * 1000
    if args.csv:
        data = closed_candles(load_csv(args.csv, interval_ms), interval_ms, int(datetime.now(timezone.utc).timestamp() * 1000))
        if data.empty:
            raise ValueError('No closed candles')
    else:
        since = int(pd.to_datetime(args.since, utc=True).timestamp() * 1000)
        data = fetch_indodax_history(ex, pair, config.market.timeframe, since, ex.milliseconds(), config.market.ohlcv_limit,
                             config.market.retries, config.market.retry_delay_seconds)
    save_csv(data, f'{args.output}/candles.csv')
    train, test = run_split(data, EMATrendStrategy(config.strategy), config)
    write_report(train, test, config, args.output)
    print(f'Report saved to {args.output}')


if __name__ == '__main__':
    main()
