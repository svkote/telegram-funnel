"""Проверка подписанных Telegram Mini App initData."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl


def validate_init_data(raw: str, token: str, max_age: int = 3600) -> int | None:
    """Проверяет HMAC, время и возвращает Telegram ID пользователя.

    Args:
        raw: Неизменённая строка window.Telegram.WebApp.initData.
        token: Секретный токен бота.
        max_age: Максимальный возраст подписи в секундах.
    Returns:
        int | None: ID пользователя при успешной проверке, иначе None.
    """
    try:
        pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True)
        data = dict(pairs)
        if len(data) != len(pairs):
            return None
        signature = data.pop('hash')
        # Поле signature предназначено для проверки Ed25519 и не участвует в HMAC.
        data.pop('signature', None)
        auth_date = int(data['auth_date'])
        if auth_date > time.time() + 60 or time.time() - auth_date > max_age:
            return None
        check_string = '\n'.join(f'{key}={value}' for key, value in sorted(data.items()))
        secret = hmac.new(b'WebAppData', token.encode(), hashlib.sha256).digest()
        expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        user_id = json.loads(data['user'])['id']
        return user_id if type(user_id) is int else None
    except (KeyError, ValueError, TypeError, json.JSONDecodeError):
        return None
