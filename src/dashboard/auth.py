"""Password hashing and in-memory dashboard sessions, without credentials in logs."""
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import threading
import time

ITERATIONS = 600_000
SESSION_SECONDS = 3600


def write_password(path: Path, password: str) -> None:
    if not isinstance(password, str) or len(password) < 16 or len(password) > 1024:
        raise ValueError('Password must contain 16 to 1024 characters')
    path.parent.mkdir(parents=True, exist_ok=True)
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, ITERATIONS)
    record = {'salt': salt.hex(), 'digest': digest.hex(), 'iterations': ITERATIONS}
    # Never silently overwrite an existing password hash.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as file:
        json.dump(record, file)


class Auth:
    def __init__(self, path: Path):
        record = json.loads(path.read_text(encoding='utf-8'))
        self.salt = bytes.fromhex(record['salt'])
        self.digest = bytes.fromhex(record['digest'])
        self.iterations = int(record['iterations'])
        if len(self.salt) != 16 or len(self.digest) != 32 or not 600_000 <= self.iterations <= 2_000_000:
            raise ValueError('Invalid dashboard authentication file')
        self.sessions: dict[str, tuple[str, float]] = {}
        self.failures: dict[str, list[float]] = {}
        self.lock = threading.Lock()

    def login(self, password: str, client: str) -> tuple[str, str] | None:
        if not isinstance(password, str) or len(password) > 1024:
            return None
        now = time.monotonic()
        with self.lock:
            attempts = [t for t in self.failures.get(client, []) if now - t < 300]
            if len(attempts) >= 5:
                self.failures[client] = attempts
                return None
            # Hold lock so concurrent attempts cannot evade the limit.
            candidate = hashlib.pbkdf2_hmac('sha256', password.encode(), self.salt, self.iterations)
            if not hmac.compare_digest(candidate, self.digest):
                self.failures[client] = attempts + [now]
                return None
            self.failures.pop(client, None)
            self.sessions = {k: v for k, v in self.sessions.items() if v[1] > now}
            if len(self.sessions) >= 32:
                self.sessions.pop(next(iter(self.sessions)))
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            self.sessions[token] = (csrf, now + SESSION_SECONDS)
            return token, csrf

    def session(self, token: str) -> str | None:
        with self.lock:
            value = self.sessions.get(token)
            if not value:
                return None
            if value[1] <= time.monotonic():
                self.sessions.pop(token, None)
                return None
            return value[0]

    def logout(self, token: str) -> None:
        with self.lock:
            self.sessions.pop(token, None)
