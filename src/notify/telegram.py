"""Optional Telegram notifications/commands. Never log token or HTTP errors."""
import requests


class Telegram:
    def __init__(self, token: str, chat_id: str, timeout: float = 10):
        if not token or not chat_id:
            raise ValueError('Telegram token and chat id required')
        self._base = f'https://api.telegram.org/bot{token}'
        self.chat_id = str(chat_id)
        self.timeout = timeout

    def send(self, text: str) -> bool:
        try:
            response = requests.post(self._base + '/sendMessage', json={'chat_id': self.chat_id, 'text': text}, timeout=self.timeout)
            return response.ok and response.json().get('ok') is True
        except (requests.RequestException, ValueError):
            return False

    def updates(self, offset: int, limit: int) -> list[dict]:
        try:
            response = requests.post(self._base + '/getUpdates', json={'offset': offset, 'limit': limit, 'timeout': 0}, timeout=self.timeout)
            if not response.ok:
                return []
            data = response.json()
            return data.get('result', []) if data.get('ok') is True and isinstance(data.get('result'), list) else []
        except (requests.RequestException, ValueError):
            return []

    def command(self, update: dict) -> str | None:
        message = update.get('message') or {}
        if str((message.get('chat') or {}).get('id')) != self.chat_id:
            return None
        text = message.get('text')
        if not isinstance(text, str):
            return None
        command = text.strip().split(' ', 1)[0].split('@', 1)[0].lower()
        return command if command in {'/status', '/pause', '/resume', '/stop'} else None
