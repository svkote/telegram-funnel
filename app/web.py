"""HTTP-сервер дашборда с Telegram-авторизацией и входом по паролю."""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from collections import defaultdict, deque
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.auth import validate_init_data
from app.config import Settings
from app.database import Database

STATIC = Path(__file__).resolve().parent.parent / 'static'
COOKIE_NAME = 'funnel_admin'
SESSION_SECONDS = 8 * 3600
ATTEMPT_WINDOW = 15 * 60
MAX_ATTEMPTS = 5


class LoginPayload(BaseModel):
    """Пароль, введённый администратором."""
    password: str


def create_app(settings: Settings, db: Database) -> FastAPI:
    """Создаёт веб-приложение с закрытой статистикой.

    Args:
        settings: Настройки бота, список администраторов и режим предпросмотра.
        db: Источник статистики.
    Returns:
        FastAPI: Веб-приложение с защищённым API.
    """
    app = FastAPI(title='Telegram Funnel Dashboard', docs_url=None, redoc_url=None)
    attempts: dict[str, deque[float]] = defaultdict(deque)
    password = os.getenv('DASHBOARD_PASSWORD', '')
    secret = hashlib.sha256(('funnel-session-v1:' + settings.bot_token + ':' + password).encode()).digest()

    def make_cookie() -> str:
        """Формирует подписанную сессию с ограниченным сроком действия.

        Returns:
            str: Значение cookie в формате expiry.signature.
        """
        expiry = str(int(time.time()) + SESSION_SECONDS)
        signature = hmac.new(secret, expiry.encode(), hashlib.sha256).hexdigest()
        return f'{expiry}.{signature}'

    def valid_cookie(value: str | None) -> bool:
        """Проверяет срок действия и HMAC cookie.

        Args:
            value: Cookie браузера или None.
        Returns:
            bool: True, если cookie подлинная и не просрочена.
        """
        if not value:
            return False
        try:
            expiry, provided = value.split('.', 1)
            if int(expiry) < time.time():
                return False
            expected = hmac.new(secret, expiry.encode(), hashlib.sha256).hexdigest()
            return hmac.compare_digest(provided, expected)
        except (ValueError, TypeError):
            return False

    def authorized(request: Request, init_data: str) -> bool:
        """Разрешает вход локальному предпросмотру, сессии или Telegram-администратору.

        Args:
            request: HTTP-запрос с cookie.
            init_data: Подписанные данные Telegram Mini App.
        Returns:
            bool: Есть ли доступ к статистике.
        """
        if settings.preview or valid_cookie(request.cookies.get(COOKIE_NAME)):
            return True
        user_id = validate_init_data(init_data, settings.bot_token)
        return user_id is not None and user_id in settings.admin_ids

    @app.get('/')
    async def index() -> FileResponse:
        """Отдаёт HTML со скрытым до входа дашбордом."""
        return FileResponse(STATIC / 'index.html')

    @app.post('/api/login')
    async def login(request: Request, payload: LoginPayload, response: Response) -> dict:
        """Проверяет пароль, ограничивает попытки и устанавливает HttpOnly cookie."""
        if not password:
            raise HTTPException(status_code=403, detail='Вход по паролю не настроен')
        # Не используем X-Forwarded-For от клиента для ограничения попыток.
        ip = request.client.host if request.client else 'unknown'
        now = time.monotonic()
        recent = attempts[ip]
        while recent and now - recent[0] > ATTEMPT_WINDOW:
            recent.popleft()
        if len(recent) >= MAX_ATTEMPTS:
            raise HTTPException(status_code=429, detail='Слишком много попыток. Попробуй через 15 минут')
        if not secrets.compare_digest(payload.password.encode(), password.encode()):
            recent.append(now)
            raise HTTPException(status_code=401, detail='Неверный пароль')
        recent.clear()
        response.set_cookie(
            key=COOKIE_NAME, value=make_cookie(), max_age=SESSION_SECONDS,
            secure=not settings.preview, httponly=True, samesite='lax', path='/',
        )
        return {'ok': True}

    @app.post('/api/logout')
    async def logout(response: Response) -> dict:
        """Удаляет cookie авторизации из браузера."""
        response.delete_cookie(COOKIE_NAME, path='/')
        return {'ok': True}

    @app.get('/api/stats')
    async def stats(request: Request, x_telegram_init_data: str = Header(default='')) -> dict:
        """Возвращает статистику только после проверки доступа."""
        if not authorized(request, x_telegram_init_data):
            raise HTTPException(status_code=403, detail='Нет доступа')
        return db.stats()

    return app
