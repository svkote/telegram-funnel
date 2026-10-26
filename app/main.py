"""Единая точка запуска Telegram polling, планировщика и веб-сервера."""
from __future__ import annotations

import asyncio
import logging

import uvicorn
from aiogram import Bot

from app.bot import build_dispatcher, delivery_worker
from app.config import load_settings
from app.database import Database
from app.web import create_app


async def main() -> None:
    """Запускает бота, HTTP-панель и доставку задач в одном процессе.

    Returns:
        None, завершается по Ctrl+C.
    """
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    settings = load_settings()
    db = Database(settings.db_path)
    db.initialize()
    db.recover_pending()
    server = uvicorn.Server(uvicorn.Config(
        create_app(settings, db), host=settings.host, port=settings.port, log_level='info'
    ))
    async with Bot(token=settings.bot_token) as bot:
        dp = build_dispatcher(settings, db)
        tasks = [
            asyncio.create_task(dp.start_polling(bot), name='bot'),
            asyncio.create_task(delivery_worker(bot, db), name='scheduler'),
            asyncio.create_task(server.serve(), name='web'),
        ]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                if not task.cancelled() and task.exception():
                    raise task.exception()
        finally:
            server.should_exit = True
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
