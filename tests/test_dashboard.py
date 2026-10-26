"""Проверки закрытого дашборда и входа по паролю."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.config import load_settings
from app.database import Database
from app.web import create_app


class DashboardAuthTests(unittest.TestCase):
    """Проверяет, что статистика недоступна без входа."""

    def test_login_flow(self) -> None:
        """Авторизация выдаёт cookie, выход снова закрывает API."""
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
            'DASHBOARD_PASSWORD': '123', 'DASHBOARD_PREVIEW': '0',
            'BOT_TOKEN': '12345:TEST', 'ADMIN_IDS': '157498184',
        }):
            settings = load_settings()
            db = Database(Path(tmp) / 'funnel.sqlite3')
            db.initialize()
            # Для cookie secure используется HTTPS, поэтому клиент тестируется через HTTPS.
            with TestClient(create_app(settings, db), base_url='https://testserver') as client:
                self.assertEqual(client.get('/api/stats').status_code, 403)
                self.assertEqual(client.post('/api/login', json={'password':'wrong'}).status_code, 401)
                response = client.post('/api/login', json={'password':'123'})
                self.assertEqual(response.status_code, 200)
                self.assertIn('httponly', response.headers['set-cookie'].lower())
                self.assertIn('secure', response.headers['set-cookie'].lower())
                self.assertEqual(client.get('/api/stats').status_code, 200)
                client.post('/api/logout')
                self.assertEqual(client.get('/api/stats').status_code, 403)
