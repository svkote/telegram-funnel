"""Тесты основных сценариев без обращения к Telegram API."""
from __future__ import annotations

import hashlib
import hmac
import json
import tempfile
import time
import unittest
from pathlib import Path
from urllib.parse import urlencode

from app.auth import validate_init_data
from app.config import ScheduledMessage, load_settings
from app.database import Database


class DatabaseTests(unittest.TestCase):
    """Проверяет регистрацию и очередь отправок."""

    def test_registration_is_idempotent(self) -> None:
        """Повторный контакт не должен создать повторную рассылку."""
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / 'test.sqlite3')
            db.initialize()
            db.record_start(123, 'Кот', 'cat')
            messages = (ScheduledMessage(0, 'Тест 1'), ScheduledMessage(0, 'Тест 2'))
            self.assertTrue(db.register(123, '+70000000000', messages))
            self.assertFalse(db.register(123, '+70000000000', messages))
            jobs = db.claim_due()
            self.assertEqual(len(jobs), 2)
            db.finish_job(jobs[0]['id'])
            stats = db.stats()
            self.assertEqual(stats['users'], 1)
            self.assertEqual(stats['registered'], 1)
            self.assertEqual(stats['sent'], 1)
            self.assertEqual(stats['pending'], 1)
            self.assertEqual(stats['conversion'], 100)
            self.assertNotIn('phone', str(stats))

    def test_recovery(self) -> None:
        """Проверяет возврат прерванной отправки после рестарта."""
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / 'test.sqlite3')
            db.initialize()
            db.record_start(1, 'A', None)
            db.register(1, '+7000', (ScheduledMessage(0, 'Hello'),))
            self.assertEqual(len(db.claim_due()), 1)
            self.assertEqual(len(db.claim_due()), 0)
            db.recover_pending()
            self.assertEqual(len(db.claim_due()), 1)


class AuthenticationTests(unittest.TestCase):
    """Проверяет Telegram-подпись и защиту от подмены."""

    def test_signed_data(self) -> None:
        """Принимает корректную подпись и отвергает изменённого пользователя."""
        token = '123:TESTTOKEN'
        data = {'auth_date': str(int(time.time())), 'user': json.dumps({'id': 42})}
        check = '\n'.join(f'{key}={val}' for key, val in sorted(data.items()))
        secret = hmac.new(b'WebAppData', token.encode(), hashlib.sha256).digest()
        data['hash'] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        self.assertEqual(validate_init_data(urlencode(data), token), 42)
        data['user'] = json.dumps({'id': 43})
        self.assertIsNone(validate_init_data(urlencode(data), token))

    def test_yaml(self) -> None:
        """Проверяет задержки и тексты из конфигурации."""
        settings = load_settings(require_token=False)
        self.assertEqual([m.delay_seconds for m in settings.messages], [10, 60])


if __name__ == '__main__':
    unittest.main()
