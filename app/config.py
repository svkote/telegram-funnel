"""Чтение и проверка настроек приложения."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class ScheduledMessage:
    """Содержит задержку в секундах и текст отправляемого сообщения."""

    delay_seconds: int
    text: str


@dataclass(frozen=True)
class Settings:
    """Хранит конфигурацию бота и веб-сервера."""

    bot_token: str
    admin_ids: frozenset[int]
    host: str
    port: int
    db_path: Path
    preview: bool
    mini_app_url: str
    welcome: str
    contact_button: str
    contact_saved: str
    contact_hint: str
    messages: tuple[ScheduledMessage, ...]


def load_settings(require_token: bool = True) -> Settings:
    """Читает .env и YAML, валидирует обязательные поля и возвращает настройки.

    Args:
        require_token: Требовать ли токен Telegram (False используется для тестов).
    Returns:
        Settings: Проверенная конфигурация приложения.
    """
    load_dotenv(ROOT / '.env')
    raw: dict[str, Any] = yaml.safe_load((ROOT / 'config.yaml').read_text(encoding='utf-8'))
    if not isinstance(raw, dict):
        raise ValueError('config.yaml должен содержать объект YAML')
    messages = raw.get('messages')
    if not isinstance(messages, list) or len(messages) != 2:
        raise ValueError('В config.yaml должны быть ровно два сообщения')
    scheduled: list[ScheduledMessage] = []
    for item in messages:
        if not isinstance(item, dict) or type(item.get('delay_seconds')) is not int or item['delay_seconds'] < 0:
            raise ValueError('delay_seconds должен быть целым неотрицательным числом')
        if not isinstance(item.get('text'), str) or not item['text'].strip():
            raise ValueError('Текст запланированного сообщения не может быть пустым')
        scheduled.append(ScheduledMessage(item['delay_seconds'], item['text']))
    for field in ('welcome', 'contact_button', 'contact_saved', 'contact_hint'):
        if not isinstance(raw.get(field), str) or not raw[field].strip():
            raise ValueError(f'Не заполнен ключ {field} в config.yaml')
    token = os.getenv('BOT_TOKEN', '').strip()
    if require_token and (not token or 'REPLACE_' in token):
        raise ValueError('Укажи реальный BOT_TOKEN в .env')
    admins = frozenset(int(x.strip()) for x in os.getenv('ADMIN_IDS', '').split(',') if x.strip())
    preview = os.getenv('DASHBOARD_PREVIEW', '0') == '1'
    host = os.getenv('WEB_HOST', '127.0.0.1')
    if preview and host not in ('127.0.0.1', 'localhost', '::1'):
        raise ValueError('DASHBOARD_PREVIEW=1 разрешен только на localhost')
    db_value = Path(os.getenv('DATABASE_PATH', 'data/funnel.sqlite3'))
    return Settings(
        bot_token=token, admin_ids=admins, host=host,
        port=int(os.getenv('WEB_PORT', '8000')),
        db_path=db_value if db_value.is_absolute() else ROOT / db_value,
        preview=preview, mini_app_url=os.getenv('MINI_APP_URL', '').strip(),
        welcome=raw['welcome'], contact_button=raw['contact_button'],
        contact_saved=raw['contact_saved'], contact_hint=raw['contact_hint'],
        messages=tuple(scheduled),
    )
