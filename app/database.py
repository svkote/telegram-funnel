"""SQLite-хранилище регистрации, событий и устойчивой очереди сообщений."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.config import ScheduledMessage


def utc_now() -> datetime:
    """Возвращает текущее UTC время с часовым поясом."""
    return datetime.now(timezone.utc)


class Database:
    """Инкапсулирует операции SQLite без внешнего ORM."""

    def __init__(self, path: Path) -> None:
        """Сохраняет путь к БД и создаёт директорию при необходимости.

        Args:
            path: Путь к файлу SQLite.
        Returns:
            None.
        """
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        """Открывает SQLite-соединение с ожиданием блокировок.

        Returns:
            sqlite3.Connection: Соединение с именованными колонками.
        """
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA busy_timeout=10000')
        return conn

    def initialize(self) -> None:
        """Создаёт таблицы и индексы при первом запуске.

        Returns:
            None.
        """
        with self.connect() as conn:
            conn.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    first_name TEXT NOT NULL,
                    username TEXT,
                    phone TEXT,
                    started_at TEXT NOT NULL,
                    registered_at TEXT
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_events_date ON events(created_at);
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    message_index INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    due_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    sent_at TEXT,
                    last_error TEXT,
                    UNIQUE(user_id, message_index)
                );
                CREATE INDEX IF NOT EXISTS idx_jobs_due ON jobs(status, due_at);
            ''')

    def record_start(self, user_id: int, first_name: str, username: str | None) -> None:
        """Сохраняет пользователя и каждое обращение /start.

        Args:
            user_id: ID Telegram.
            first_name: Имя пользователя.
            username: Username, если есть.
        Returns:
            None.
        """
        now = utc_now().isoformat()
        with self.connect() as conn:
            conn.execute('''
                INSERT INTO users(user_id, first_name, username, started_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    first_name=excluded.first_name, username=excluded.username
            ''', (user_id, first_name, username, now))
            conn.execute('INSERT INTO events(user_id,event_type,created_at) VALUES (?, ?, ?)',
                         (user_id, 'start', now))

    def register(self, user_id: int, phone: str, messages: tuple[ScheduledMessage, ...]) -> bool:
        """Однократно регистрирует пользователя и создаёт два отложенных задания.

        Args:
            user_id: ID Telegram.
            phone: Контактный номер самого пользователя.
            messages: Сообщения из YAML с задержками.
        Returns:
            bool: True только при первой регистрации.
        """
        now = utc_now()
        with self.connect() as conn:
            cursor = conn.execute('''
                UPDATE users SET phone=?, registered_at=?
                WHERE user_id=? AND registered_at IS NULL
            ''', (phone, now.isoformat(), user_id))
            if not cursor.rowcount:
                return False
            conn.execute('INSERT INTO events(user_id,event_type,created_at) VALUES (?, ?, ?)',
                         (user_id, 'registration', now.isoformat()))
            for index, item in enumerate(messages, start=1):
                due = (now + timedelta(seconds=item.delay_seconds)).isoformat()
                conn.execute('''
                    INSERT INTO jobs(user_id,message_index,text,due_at)
                    VALUES (?, ?, ?, ?)
                ''', (user_id, index, item.text, due))
        return True

    def claim_due(self, limit: int = 25) -> list[dict[str, Any]]:
        """Резервирует созревшие задания атомарно, не допуская двойной отправки.

        Args:
            limit: Максимум заданий за один проход.
        Returns:
            list[dict]: Зарезервированные задания.
        """
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            rows = conn.execute('''
                SELECT id, user_id, text FROM jobs
                WHERE status='pending' AND due_at<=?
                ORDER BY due_at, id LIMIT ?
            ''', (utc_now().isoformat(), limit)).fetchall()
            if rows:
                conn.executemany("UPDATE jobs SET status='sending', attempts=attempts+1 WHERE id=?",
                                 [(row['id'],) for row in rows])
            return [dict(row) for row in rows]

    def finish_job(self, job_id: int, error: str | None = None) -> None:
        """Отмечает сообщение отправленным или сохраняет ошибку доставки.

        Args:
            job_id: ID задания.
            error: Описание ошибки, если отправка не удалась.
        Returns:
            None.
        """
        with self.connect() as conn:
            if error is None:
                conn.execute("UPDATE jobs SET status='sent', sent_at=? WHERE id=?",
                             (utc_now().isoformat(), job_id))
            else:
                conn.execute("UPDATE jobs SET status='failed', last_error=? WHERE id=?",
                             (error[:500], job_id))

    def recover_pending(self) -> None:
        """Возвращает прерванные отправки в очередь после перезапуска.

        Returns:
            None.
        """
        with self.connect() as conn:
            conn.execute("UPDATE jobs SET status='pending' WHERE status='sending'")

    def stats(self) -> dict[str, Any]:
        """Возвращает агрегаты и последние регистрации без раскрытия телефонов.

        Returns:
            dict: Счётчики и список последних пользователей.
        """
        with self.connect() as conn:
            metrics = conn.execute('''
                SELECT (SELECT COUNT(*) FROM users) AS users,
                       (SELECT COUNT(*) FROM users WHERE registered_at IS NOT NULL) AS registered,
                       (SELECT COUNT(*) FROM events WHERE event_type='start') AS starts,
                       (SELECT COUNT(*) FROM jobs WHERE status='sent') AS sent,
                       (SELECT COUNT(*) FROM jobs WHERE status='pending' OR status='sending') AS pending,
                       (SELECT COUNT(*) FROM jobs WHERE status='failed') AS failed
            ''').fetchone()
            recent = conn.execute('''
                SELECT user_id, first_name, username, registered_at
                FROM users WHERE registered_at IS NOT NULL
                ORDER BY registered_at DESC LIMIT 10
            ''').fetchall()
            days = conn.execute('''
                SELECT substr(registered_at, 1, 10) AS day, COUNT(*) AS amount
                FROM users WHERE registered_at IS NOT NULL
                GROUP BY day ORDER BY day DESC LIMIT 7
            ''').fetchall()
        payload = dict(metrics)
        payload['conversion'] = round(payload['registered'] / payload['users'] * 100, 1) if payload['users'] else 0
        payload['recent'] = [dict(row) for row in recent]
        payload['daily'] = [dict(row) for row in reversed(days)]
        return payload
