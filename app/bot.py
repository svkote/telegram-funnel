"""Обработчики aiogram и обработчик отложенных отправок."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup, ReplyKeyboardRemove, WebAppInfo

from app.config import Settings
from app.database import Database

logger = logging.getLogger(__name__)
router = Router()


def build_dispatcher(settings: Settings, db: Database) -> Dispatcher:
    """Создаёт диспетчер и передаёт зависимости обработчикам.

    Args:
        settings: Конфигурация из YAML и .env.
        db: Хранилище SQLite.
    Returns:
        Dispatcher: Настроенный обработчик входящих сообщений.
    """
    dp = Dispatcher()
    dp.include_router(router)
    dp['settings'] = settings
    dp['db'] = db
    return dp


@router.message(CommandStart())
async def on_start(message: Message, settings: Settings, db: Database) -> None:
    """Сохраняет открытие бота и предлагает пользователю поделиться телефоном.

    Args:
        message: Сообщение /start.
        settings: Тексты и адрес мини-аппа.
        db: База пользователей.
    Returns:
        None.
    """
    if not message.from_user or message.chat.type != 'private':
        return
    user = message.from_user
    db.record_start(user.id, user.first_name, user.username)
    buttons = [[KeyboardButton(text=settings.contact_button, request_contact=True)]]
    if settings.mini_app_url and user.id in settings.admin_ids:
        buttons.append([KeyboardButton(text='📊 Статистика', web_app=WebAppInfo(url=settings.mini_app_url))])
    await message.answer(settings.welcome, reply_markup=ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True))


@router.message(F.contact)
async def on_contact(message: Message, settings: Settings, db: Database) -> None:
    """Принимает только собственный контакт, сохраняет регистрацию один раз.

    Args:
        message: Сообщение с контактом Telegram.
        settings: Конфигурация рассылки.
        db: База пользователей.
    Returns:
        None.
    """
    if not message.from_user or message.chat.type != 'private' or not message.contact:
        return
    if message.contact.user_id != message.from_user.id:
        await message.answer('Нужно отправить именно свой контакт через кнопку 👇')
        return
    created = db.register(message.from_user.id, message.contact.phone_number, settings.messages)
    await message.answer(
        settings.contact_saved if created else 'Ты уже зарегистрирован(а) ✅',
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message()
async def on_other(message: Message, settings: Settings) -> None:
    """Напоминает о кнопке контакта при произвольном тексте.

    Args:
        message: Произвольное сообщение.
        settings: Настройки с текстом подсказки.
    Returns:
        None.
    """
    if message.chat.type == 'private':
        await message.answer(settings.contact_hint)


async def delivery_worker(bot: Bot, db: Database, interval: float = 1.0) -> None:
    """Проверяет SQLite-очередь и отправляет запланированные сообщения.

    Args:
        bot: Telegram API клиент.
        db: SQLite с заданиями.
        interval: Период опроса очереди в секундах.
    Returns:
        None; работает до отмены приложения.
    """
    while True:
        for job in db.claim_due():
            try:
                await bot.send_message(chat_id=job['user_id'], text=job['text'])
            except Exception as exc:
                logger.exception('Ошибка отправки задания %s', job['id'])
                # Ошибки сохраняем; слепой повтор может продублировать сообщение.
                db.finish_job(job['id'], str(exc))
            else:
                db.finish_job(job['id'])
        await asyncio.sleep(interval)
