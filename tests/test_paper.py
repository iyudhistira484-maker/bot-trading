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
    assert order['fee_tax'] == pytest.approx(order['price'] * (config.execution.taker_buy_fee_rate + config.execution.buy_tax_rate))
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


def test_paper_uses_separate_buy_sell_rates_for_fill_and_equity(paper, quote, now):
    cfg = paper.config.execution
    cfg.taker_buy_fee_rate = .001
    cfg.buy_tax_rate = .002
    cfg.taker_sell_fee_rate = .004
    cfg.sell_tax_rate = .006
    quotes = {'BTC/IDR': quote}
    buy = paper.place_order('asym-buy', 'BTC/IDR', 'buy', 1, 90, quotes, now)
    assert buy['fee_tax'] == pytest.approx(buy['price'] * .003)
    cash = paper.balances()['IDR']
    _, marked_equity = paper.assess_risk(quotes, now)
    expected_sell_price = quote.bid * (1 - cfg.slippage_rate)
    assert marked_equity == pytest.approx(cash + expected_sell_price * .99)
    sell = paper.place_order('asym-sell', 'BTC/IDR', 'sell', 1, None, quotes, now)
    assert sell['fee_tax'] == pytest.approx(sell['price'] * .01)
    assert paper.balances()['IDR'] == pytest.approx(cash + sell['price'] * .99)


def test_cap_enforced_across_pairs_including_entry_fees_and_released_on_exit(paper, quote, now):
    paper.config.market.pairs.append('ETH/IDR')
    paper.config.risk.max_capital_idr = 150
    q = {'BTC/IDR': quote, 'ETH/IDR': quote}
    first = paper.place_order('cap-first', 'BTC/IDR', 'buy', 1, 90, q, now)
    invested = first['amount'] * first['price'] + first['fee_tax']
    assert paper.remaining_entry_capital() == pytest.approx(150 - invested)
    before = paper.balances()
    with pytest.raises(OrderRejected, match='Maximum invested capital'):
        paper.place_order('cap-denied', 'ETH/IDR', 'buy', .5, 90, q, now)
    assert paper.balances() == before
    second = paper.place_order('cap-second', 'ETH/IDR', 'buy', .4, 90, q, now)
    combined = invested + second['amount'] * second['price'] + second['fee_tax']
    assert combined <= 150
    paper.place_order('cap-exit', 'BTC/IDR', 'sell', 1, None, q, now)
    assert paper.remaining_entry_capital() == pytest.approx(150 - (second['amount'] * second['price'] + second['fee_tax']))


def test_cap_counts_fees_and_allows_exit_if_cap_lowered(paper, quote, now):
    paper.config.risk.max_capital_idr = 101.2  # Gross < cap, but gross + fees > cap.
    with pytest.raises(OrderRejected, match='Maximum invested capital'):
        paper.place_order('fee-limit', 'BTC/IDR', 'buy', 1, 90, {'BTC/IDR': quote}, now)
    paper.config.risk.max_capital_idr = 200
    paper.place_order('fee-allowed', 'BTC/IDR', 'buy', 1, 90, {'BTC/IDR': quote}, now)
    paper.config.risk.max_capital_idr = 1
    assert paper.remaining_entry_capital() == 0
    paper.place_order('cap-lowered-exit', 'BTC/IDR', 'sell', 1, None, {'BTC/IDR': quote}, now)
    assert not paper.balances()['positions']


def test_missing_or_corrupt_old_entry_blocks_new_buys_not_sells(paper, quote, now):
    paper.config.market.pairs.append('ETH/IDR')
    q = {'BTC/IDR': quote, 'ETH/IDR': quote}
    paper.place_order('old-entry', 'BTC/IDR', 'buy', 1, 90, q, now)
    paper.db.execute("UPDATE orders SET result=? WHERE client_id='old-entry'", ('{}',))
    with pytest.raises(OrderRejected, match='entry cost unavailable'):
        paper.place_order('new-entry', 'ETH/IDR', 'buy', 1, 90, q, now)
    paper.db.execute("DELETE FROM orders WHERE client_id='old-entry'")
    with pytest.raises(OrderRejected, match='entry cost unavailable'):
        paper.remaining_entry_capital()
    paper.place_order('old-exit', 'BTC/IDR', 'sell', 1, None, q, now)
    assert not paper.balances()['positions']


def test_cap_transaction_serializes_competing_purchases(tmp_path, config, quote, now):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    config.market.pairs.append('ETH/IDR')
    config.risk.max_capital_idr = 150
    path = tmp_path / 'race.sqlite'
    initial = PaperExecutor(path, FakeExchange(), config, initial_cash=10000)
    initial.close()
    barrier = threading.Barrier(2)
    def buy(symbol):
        executor = PaperExecutor(path, FakeExchange(), config, initial_cash=10000)
        try:
            barrier.wait(timeout=10)
            q = {'BTC/IDR': quote, 'ETH/IDR': quote}
            try:
                executor.place_order('race-' + symbol, symbol, 'buy', 1, 90, q, now)
                return 'filled'
            except OrderRejected as e:
                assert 'Maximum invested capital' in str(e)
                return 'blocked'
        finally:
            executor.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(buy, ['BTC/IDR', 'ETH/IDR']))
    assert sorted(results) == ['blocked', 'filled']
    reader = PaperExecutor(path, FakeExchange(), config, initial_cash=10000)
    try:
        assert len(reader.balances()['positions']) == 1
        assert 0 <= reader.remaining_entry_capital() < 50
    finally:
        reader.close()


def test_open_cost_cap_persists_on_restart_and_does_not_count_market_appreciation(tmp_path, paper, quote, now, config):
    paper.config.market.pairs.append('ETH/IDR')
    paper.config.risk.max_capital_idr = 150
    original_q = {'BTC/IDR': quote, 'ETH/IDR': quote}
    buy = paper.place_order('restart-cap', 'BTC/IDR', 'buy', 1, 90, original_q, now)
    remaining = 150 - buy['price'] * buy['amount'] - buy['fee_tax']
    paper.close()
    paper.db = PaperExecutor(tmp_path / 'paper.sqlite', FakeExchange(), config).db
    # Market appreciation changes equity, not the stored purchase-cost basis.
    high = Quote(149, 151, 150, now, now-1000, 150)
    q = {'BTC/IDR': high, 'ETH/IDR': quote}
    assert paper.remaining_entry_capital() == pytest.approx(remaining)
    assert paper.place_order('restart-second', 'ETH/IDR', 'buy', .4, 90, q, now)['status'] == 'closed'


def test_replaying_same_client_id_does_not_double_count_cap(paper, quote, now):
    paper.config.risk.max_capital_idr = 150
    q = {'BTC/IDR': quote}
    original = paper.place_order('same-cap', 'BTC/IDR', 'buy', 1, 90, q, now)
    before = paper.remaining_entry_capital()
    assert paper.place_order('same-cap', 'BTC/IDR', 'buy', 1, 90, q, now) == original
    assert paper.remaining_entry_capital() == before
