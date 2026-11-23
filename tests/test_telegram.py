import requests

from src.notify.telegram import Telegram


def test_authorized_commands_only():
    tg = Telegram('secret-token', '123')
    assert tg.command({'message': {'chat': {'id': 123}, 'text': '/pause'}}) == '/pause'
    assert tg.command({'message': {'chat': {'id': 124}, 'text': '/pause'}}) is None
    assert tg.command({'message': {'chat': {'id': 123}, 'text': 'hello'}}) is None


def test_telegram_timeout_is_sanitized(monkeypatch, capsys):
    def fail(*args, **kwargs): raise requests.Timeout('secret-token in URL')
    monkeypatch.setattr(requests, 'post', fail)
    tg = Telegram('secret-token', '123')
    assert not tg.send('hello')
    assert tg.updates(0, 20) == []
    assert 'secret-token' not in capsys.readouterr().err
