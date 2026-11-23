from datetime import datetime, timezone
import pytest

from src.execution.paper import PaperExecutor, Quote, OrderRejected


class FakeExchange:
    def market(self, symbol):
        return {'spot': True, 'active': True, 'quote': 'IDR', 'limits': {'amount': {'min': .01}, 'price': {'min': 1}, 'cost': {'min': 5}}}
    def amount_to_precision(self, symbol, amount):
        return f'{int(amount * 100) / 100:.2f}'
    def parse_timeframe(self, timeframe): return 3600


@pytest.fixture
def now(): return int(datetime.now(timezone.utc).timestamp() * 1000)


@pytest.fixture
def quote(now): return Quote(99, 101, 100, now, now-1000, 100)


@pytest.fixture
def paper(tmp_path, config):
    ex = PaperExecutor(tmp_path / 'paper.sqlite', FakeExchange(), config, initial_cash=10000)
    yield ex
    ex.close()


def test_fills_fees_persist_and_idempotent(tmp_path, paper, quote, now, config):
    q = {'BTC/IDR': quote}
    order = paper.place_order('buy1', 'BTC/IDR', 'buy', 1, 90, q, now)
    assert order['price'] == pytest.approx(101 * (1 + config.execution.slippage_rate))
    assert order['fee_tax'] == pytest.approx(order['price'] * (config.execution.taker_fee_rate + config.execution.tax_rate))
    cash = paper.balances()['IDR']
    assert paper.place_order('buy1', 'BTC/IDR', 'buy', 1, 90, q, now) == order
    assert paper.balances()['IDR'] == cash
    with pytest.raises(OrderRejected): paper.place_order('buy1', 'BTC/IDR', 'buy', 2, 90, q, now)
    paper.close()
    paper.db = PaperExecutor(tmp_path / 'paper.sqlite', FakeExchange(), config, initial_cash=10).db
    assert paper.balances()['IDR'] == pytest.approx(cash)
    sell = paper.place_order('sell1', 'BTC/IDR', 'sell', 1, None, q, now)
    assert sell['price'] == pytest.approx(99 * (1 - config.execution.slippage_rate))
    assert not paper.balances()['positions']
    assert paper.balances()['IDR'] == pytest.approx(cash + sell['price'] - sell['fee_tax'])


def test_rejections_do_not_change_balances(paper, quote, now):
    q = {'BTC/IDR': quote}
    initial = paper.balances()
    for amount, stop in [(0.001, 90), (1.001, 90), (10, 90), (1, 102)]:
        with pytest.raises(OrderRejected): paper.place_order(str(amount)+str(stop), 'BTC/IDR', 'buy', amount, stop, q, now)
    with pytest.raises(OrderRejected): paper.place_order('short', 'BTC/IDR', 'sell', 1, None, q, now)
    assert paper.balances() == initial
    paper.place_order('ok', 'BTC/IDR', 'buy', 1, 90, q, now)
    with pytest.raises(OrderRejected): paper.place_order('no-average', 'BTC/IDR', 'buy', 1, 90, q, now)
    with pytest.raises(OrderRejected): paper.place_order('partial', 'BTC/IDR', 'sell', .5, None, q, now)


def test_stale_future_spread_and_missing(paper, quote, now):
    qs = [Quote(99, 101, 100, now-31000, now-1000, 100), Quote(99, 101, 100, now, now+1, 100),
          Quote(80, 120, 100, now, now-1000, 100), Quote(99, 101, 100, now, now-7200001, 100), Quote(99, 101, 100, now, now-1000, 130)]
    for index, q in enumerate(qs):
        with pytest.raises(OrderRejected): paper.place_order(str(index), 'BTC/IDR', 'buy', 1, 90, {'BTC/IDR': q}, now)
    with pytest.raises(OrderRejected): paper.place_order('none', 'BTC/IDR', 'buy', 1, 90, {}, now)


def test_kill_persists_across_restart_and_rejected_order(paper, quote, now, tmp_path, config):
    q = {'BTC/IDR': quote}
    paper.place_order('open', 'BTC/IDR', 'buy', 1, 90, q, now)
    # Simulate a severe high-water drawdown via persisted prior peak.
    paper.db.execute('UPDATE risk_state SET peak=20000')
    with pytest.raises(OrderRejected): paper.place_order('denied', 'BTC/IDR', 'buy', 1, 90, q, now)
    assert paper.db.execute('SELECT killed FROM risk_state').fetchone()['killed'] == 1
    paper.close()
    paper.db = PaperExecutor(tmp_path / 'paper.sqlite', FakeExchange(), config).db
    with pytest.raises(OrderRejected): paper.place_order('again', 'BTC/IDR', 'buy', 1, 90, q, now)
    paper.place_order('liquidate', 'BTC/IDR', 'sell', 1, None, q, now)
    assert not paper.balances()['positions']


def test_kill_persisted_even_when_sell_rejected(paper, quote, now):
    q = {'BTC/IDR': quote}
    paper.place_order('open', 'BTC/IDR', 'buy', 1, 90, q, now)
    paper.db.execute('UPDATE risk_state SET peak=20000')
    with pytest.raises(OrderRejected): paper.place_order('bad-close', 'BTC/IDR', 'sell', .5, None, q, now)
    assert paper.db.execute('SELECT killed FROM risk_state').fetchone()['killed'] == 1


def test_unknown_notional_fails_closed(paper, quote, now):
    original = paper.exchange.market
    def market(symbol):
        info = original(symbol)
        info['limits']['cost']['min'] = None
        return info
    paper.exchange.market = market
    with pytest.raises(OrderRejected, match='Minimum notional unknown'):
        paper.place_order('unknown', 'BTC/IDR', 'buy', 1, 90, {'BTC/IDR': quote}, now)
    paper.config.execution.min_notional_idr['BTC/IDR'] = 5
    assert paper.place_order('known', 'BTC/IDR', 'buy', 1, 90, {'BTC/IDR': quote}, now)['status'] == 'closed'
