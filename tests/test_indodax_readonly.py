"""Offline tests only: no real credentials, private API calls, or orders."""
import json
import subprocess
import sys
from urllib.parse import parse_qs, urlsplit

import pytest
import requests

from src.indodax_readonly.client import ReadOnlyClient, ReadOnlyError, Snapshot


ACCOUNT = {'canTrade': False, 'canWithdraw': False, 'canWithdrawFiat': False,
           'balances': [{'asset': 'IDR', 'free': '12345.00', 'locked': '10'},
                        {'asset': 'BTC', 'free': '0.000001', 'locked': '0'}], 'uid': 'PRIVATE'}


class Response:
    def __init__(self, value, status=200):
        self.content = value if isinstance(value, bytes) else json.dumps(value).encode()
        self.status_code = status
        self.closed = False

    def iter_content(self, chunk_size):
        yield self.content

    def close(self): self.closed = True


class Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.trust_env = True
        self.closed = False

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception): raise response
        return response

    def close(self): self.closed = True


def test_snapshot_uses_only_v2_gets_signed_and_hides_other_data():
    responses = [Response(ACCOUNT), Response([{'symbol': 'BTCIDR', 'orderId': 1}])]
    session = Session(responses)
    client = ReadOnlyClient('fake-key', 'fake-secret', session=session, clock=lambda: 1578304294)
    result = client.inspect()
    assert result == Snapshot({'IDR': ('12345.00', '10'), 'BTC': ('0.000001', '0')}, 1)
    assert session.trust_env is False
    assert len(session.calls) == 2
    for (url, options), path in zip(session.calls, ['/api/v2/account', '/api/v2/openOrders']):
        parsed = urlsplit(url)
        assert parsed.scheme == 'https' and parsed.netloc == 'api.indodax.com' and parsed.path == path
        assert parse_qs(parsed.query) == {'timestamp': ['1578304294000'], 'recvWindow': ['5000']}
        assert options['headers']['Sign'] == 'cbb4209e5e0cc5ab84d8ffa1c302bd92bda0e957f1db54e00c7719891b34dffe'
        assert options['headers']['X-APIKEY'] == 'fake-key'
        assert options['allow_redirects'] is False and options['verify'] is True
        assert options['stream'] is True and options['timeout'] == (5, 15)
    assert all(r.closed for r in responses)
    client.close(); assert session.closed and client._secret == b'' and client._api_key == ''


@pytest.mark.parametrize('field,value', [('canTrade', True), ('canWithdraw', True),
                                        ('canWithdrawFiat', True), ('canTrade', None),
                                        ('canWithdraw', 'false'), ('canWithdrawFiat', 0)])
def test_non_view_only_or_unknown_permissions_prevent_further_calls(field, value):
    account = ACCOUNT | {field: value}
    session = Session([Response(account)])
    client = ReadOnlyClient('k', 's', session=session)
    with pytest.raises(ReadOnlyError, match='view-only'): client.inspect()
    assert len(session.calls) == 1


def test_missing_permission_not_interpreted_as_false():
    account = dict(ACCOUNT); del account['canWithdrawFiat']
    session = Session([Response(account)])
    with pytest.raises(ReadOnlyError): ReadOnlyClient('k', 's', session=session).inspect()
    assert len(session.calls) == 1


def test_missing_assets_remain_unknown():
    session = Session([Response(ACCOUNT | {'balances': []}), Response([])])
    result = ReadOnlyClient('k', 's', session=session).inspect()
    assert result.balances == {'IDR': None, 'BTC': None}
    assert result.open_orders == 0


@pytest.mark.parametrize('balances', [None, {}, [{}], [{'asset': 'IDR', 'free':'NaN','locked':'0'}],
    [{'asset':'IDR','free':'-1','locked':'0'}], [{'asset':'BTC','free':'1','locked':None}],
    [{'asset':'BTC','free':'1','locked':'0'}, {'asset':'BTC','free':'1','locked':'0'}]])
def test_rejects_invalid_balance(balances):
    session = Session([Response(ACCOUNT | {'balances': balances})])
    with pytest.raises(ReadOnlyError): ReadOnlyClient('k', 's', session=session).inspect()
    assert len(session.calls) == 1


@pytest.mark.parametrize('orders', [None, {}, [1], {'code': -1002}])
def test_rejects_invalid_open_orders(orders):
    session = Session([Response(ACCOUNT), Response(orders)])
    with pytest.raises(ReadOnlyError): ReadOnlyClient('k', 's', session=session).inspect()


def test_endpoint_allowlist_and_network_failure_are_fail_closed():
    session = Session([requests.Timeout('fake-key fake-secret')])
    client = ReadOnlyClient('fake-key', 'fake-secret', session=session)
    for path in ['/api/v2/order', 'https://evil.example', '/api/v2/myTrades', '/api/v2/account?x=1']:
        with pytest.raises(ReadOnlyError): client._get(path)
    assert not session.calls
    with pytest.raises(ReadOnlyError) as error: client.inspect()
    assert 'fake-key' not in str(error.value) and 'fake-secret' not in str(error.value)
    assert len(session.calls) == 1


@pytest.mark.parametrize('response', [Response({}, status=301), Response({}, status=403),
                                      Response(b'invalid JSON'), Response(b'{"canTrade":true,"canTrade":false}'), Response(b'x' * 2_000_001)])
def test_bad_http_or_response_rejected_and_closed(response):
    session = Session([response])
    with pytest.raises(ReadOnlyError): ReadOnlyClient('k', 's', session=session).inspect()
    assert response.closed and len(session.calls) == 1


def test_noninteractive_cli_does_not_accept_credentials_or_connect():
    proc = subprocess.run([sys.executable, '-m', 'src.indodax_readonly'], input='fake-key\nfake-secret\n',
                          capture_output=True, text=True, timeout=10)
    assert proc.returncode == 1 and 'Terminal interaktif diperlukan' in proc.stderr
    assert 'fake-key' not in proc.stdout + proc.stderr and 'fake-secret' not in proc.stdout + proc.stderr


def test_cli_hides_credentials_and_balances_by_default(monkeypatch, capsys):
    from src.indodax_readonly import __main__ as cli
    monkeypatch.setattr(cli.sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr(cli.sys.stderr, 'isatty', lambda: True)
    monkeypatch.setattr('builtins.input', lambda _: 'READ ONLY')
    answers = iter(['fake-key', 'fake-secret'])
    monkeypatch.setattr(cli.getpass, 'getpass', lambda _: next(answers))
    class FakeClient:
        def __init__(self, key, secret): assert (key, secret) == ('fake-key', 'fake-secret')
        def inspect(self): return Snapshot({'IDR': ('12345.00', '10'), 'BTC': None}, 1)
        def close(self): pass
    monkeypatch.setattr(cli, 'ReadOnlyClient', FakeClient)
    monkeypatch.setattr(cli.logging, 'disable', lambda _: None)
    assert cli.main([]) == 0
    text = capsys.readouterr().out
    assert '12345' not in text and 'fake-key' not in text and 'fake-secret' not in text
    assert 'data saldo tersedia' in text and 'bukan dianggap nol' in text
