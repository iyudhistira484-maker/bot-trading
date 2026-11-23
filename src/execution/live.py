"""Deliberately disabled live execution until exchange reconciliation is verified."""


class LiveExecutor:
    """Fail closed: never submit an order or pretend ambiguous orders are safe."""
    def __init__(self, *args, **kwargs):
        raise RuntimeError('Live execution is disabled: reconciliation and uncertain/partial order handling have not been verified')

    def place_order(self, client_id, symbol, side, amount, stop, quotes, now_ms):
        raise RuntimeError('Live execution is disabled')

    def balances(self):
        raise RuntimeError('Live execution is disabled')

    def assess_risk(self, quotes, now_ms):
        raise RuntimeError('Live execution is disabled')

    def close(self):
        return None
