"""One-shot, strictly read-only Indodax TAPI v2 account inspection.

This is a diagnostic, not a live executor or reconciliation engine. No method in
this module creates, cancels, or modifies orders. API behavior remains unverified
against a real account. Do not use these snapshots to authorize live trading.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import requests

BASE_URL = 'https://api.indodax.com'
ALLOWED_PATHS = frozenset({'/api/v2/account', '/api/v2/openOrders'})
MAX_RESPONSE_BYTES = 2_000_000


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


class ReadOnlyError(Exception):
    """An intentionally non-sensitive diagnostic failure."""


@dataclass(frozen=True)
class Snapshot:
    # None means the exchange omitted the asset; never treat omission as zero.
    balances: dict[str, tuple[str, str] | None]
    open_orders: int


class ReadOnlyClient:
    def __init__(self, api_key: str, secret: str, *, session=None, clock=time.time):
        if not isinstance(api_key, str) or not isinstance(secret, str) or not api_key or not secret:
            raise ReadOnlyError('API key dan secret diperlukan.')
        if len(api_key) > 1024 or len(secret) > 1024 or any(c in api_key for c in '\r\n'):
            raise ReadOnlyError('Format kredensial tidak valid.')
        self._api_key = api_key
        self._secret = secret.encode('utf-8')
        self._clock = clock
        self._session = session if session is not None else requests.Session()
        # Never inherit HTTP(S)_PROXY, .netrc or custom CA settings from environment.
        self._session.trust_env = False

    def close(self):
        self._session.close()
        self._api_key = ''
        self._secret = b''

    def _get(self, path: str):
        if path not in ALLOWED_PATHS:
            raise ReadOnlyError('Endpoint tidak diizinkan.')
        query = urlencode([('timestamp', int(self._clock() * 1000)), ('recvWindow', 5000)])
        signature = hmac.new(self._secret, query.encode('ascii'), hashlib.sha256).hexdigest()
        headers = {'Accept': 'application/json', 'X-APIKEY': self._api_key, 'Sign': signature}
        response = None
        try:
            # No redirects: custom API headers must never be forwarded elsewhere.
            response = self._session.get(BASE_URL + path + '?' + query, headers=headers,
                                         timeout=(5, 15), allow_redirects=False, stream=True, verify=True)
            if response.status_code != 200:
                raise ReadOnlyError('Permintaan ditolak atau gagal. Periksa izin API, jam VPS, dan koneksi.')
            payload = bytearray()
            for chunk in response.iter_content(chunk_size=65536):
                payload.extend(chunk)
                if len(payload) > MAX_RESPONSE_BYTES:
                    raise ReadOnlyError('Respons terlalu besar; pemeriksaan dihentikan.')
            return json.loads(payload, object_pairs_hook=_unique_object,
                              parse_constant=lambda _: (_ for _ in ()).throw(ValueError('invalid constant')))
        except ReadOnlyError:
            raise
        except (requests.RequestException, ValueError, UnicodeError, TypeError):
            # Never include raw server bodies, headers, URLs, or exception text.
            raise ReadOnlyError('Koneksi atau respons API tidak valid; pemeriksaan dihentikan.') from None
        finally:
            if response is not None:
                response.close()

    def inspect(self) -> Snapshot:
        account = self._get('/api/v2/account')
        if not isinstance(account, dict):
            raise ReadOnlyError('Respons akun tidak dikenali.')
        # Fail closed if permission fields are missing, malformed, or not view-only.
        permissions = ('canTrade', 'canWithdraw', 'canWithdrawFiat')
        if any(account.get(name) is not False for name in permissions):
            raise ReadOnlyError('Izin tidak terverifikasi sebagai view-only. Jangan gunakan key trade atau withdraw untuk tes ini.')
        raw_balances = account.get('balances')
        if not isinstance(raw_balances, list):
            raise ReadOnlyError('Respons saldo tidak dikenali.')
        balances: dict[str, tuple[str, str] | None] = {'IDR': None, 'BTC': None}
        seen = set()
        for item in raw_balances:
            if not isinstance(item, dict) or not isinstance(item.get('asset'), str):
                raise ReadOnlyError('Respons saldo tidak dikenali.')
            asset = item['asset']
            if asset not in balances:
                continue
            if asset in seen:
                raise ReadOnlyError('Respons saldo duplikat.')
            seen.add(asset)
            values = []
            for key in ('free', 'locked'):
                value = item.get(key)
                if not isinstance(value, str) or len(value) > 128:
                    raise ReadOnlyError('Respons saldo tidak dikenali.')
                try:
                    parsed = Decimal(value)
                except InvalidOperation:
                    raise ReadOnlyError('Respons saldo tidak dikenali.') from None
                if not parsed.is_finite() or parsed < 0:
                    raise ReadOnlyError('Respons saldo tidak dikenali.')
                values.append(str(parsed))
            balances[asset] = (values[0], values[1])
        orders = self._get('/api/v2/openOrders')
        if not isinstance(orders, list) or any(not isinstance(order, dict) for order in orders):
            raise ReadOnlyError('Respons order terbuka tidak dikenali.')
        return Snapshot(balances, len(orders))
