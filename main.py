from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.types import Message

from config import ConfigError, Settings, load_settings
from google_sheets import GoogleSheetsClient, GoogleSheetsError
from poll_sender import PollSender
from scheduler import QuizScheduler


APP_VERSION = "2026-06-27-no-catchup"


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def create_command_router(
    settings: Settings,
    poll_sender: PollSender,
    logger: logging.Logger,
) -> Router:
    router = Router(name="commands")

    @router.message(Command("send_now"))
    async def send_now_handler(message: Message) -> None:
        logger.info("Manual /send_now command received from chat %s", message.chat.id)

        from_user = message.from_user
        if settings.send_now_user_id is not None:
            if from_user is None or from_user.id != settings.send_now_user_id:
                logger.warning(
                    "Unauthorized /send_now attempt from user %s in chat %s",
                    None if from_user is None else from_user.id,
                    message.chat.id,
                )
                await message.answer("У вас нет доступа к этой команде.")
                return

        try:
            sent = await poll_sender.send_next_poll()
        except GoogleSheetsError as exc:
            logger.warning("Manual poll sending failed because Google Sheets is unavailable: %s", exc)
            await message.answer("Google Sheets временно недоступен. Попробуйте еще раз позже.")
            return
        except TelegramRetryAfter as exc:
            logger.warning("Manual poll sending hit Telegram flood limit: %s", exc)
            await message.answer("Telegram временно ограничил отправку. Попробуйте позже.")
            return
        except (TelegramBadRequest, TelegramForbiddenError) as exc:
            logger.warning("Manual poll sending failed because Telegram rejected the request: %s", exc)
            await message.answer("Telegram не принял опрос. Проверьте CHAT_ID и права бота в чате.")
            return
        except Exception:
            logger.exception("Manual poll sending failed")
            await message.answer("Не удалось отправить опрос. Подробности записаны в лог.")
            return

        if sent:
            await message.answer("Опрос отправлен.")
        else:
            await message.answer("Нет доступных вопросов со статусом NOT_SENT.")

    @router.message(Command("chatid"))
    async def chat_id_handler(message: Message) -> None:
        user_id = None if message.from_user is None else message.from_user.id
        logger.info(
            "Manual /chatid command received from chat %s by user %s",
            message.chat.id,
            user_id,
        )
        await message.answer(f"chat_id: {message.chat.id}\nuser_id: {user_id}")

    return router


async def main() -> None:
    configure_logging()
    logger = logging.getLogger(__name__)
    logger.info("Bot startup version=%s", APP_VERSION)

    try:
        settings = load_settings()
        settings.zone_info
    except ConfigError:
        logger.exception("Configuration error")
        raise

    bot = Bot(token=settings.bot_token)
    dispatcher = Dispatcher()

    sheets_client = GoogleSheetsClient(
        credentials_path=settings.credentials_path,
        credentials_json=settings.google_credentials_json,
        credentials_base64=settings.google_credentials_base64,
        sheet_id=settings.google_sheet_id,
        sheet_name=settings.google_sheet_name,
    )
    try:
        await asyncio.to_thread(sheets_client.connect)
        await asyncio.to_thread(sheets_client.ensure_service_columns)
    except GoogleSheetsError as exc:
        logger.warning(
            "Google Sheets is unavailable during startup. "
            "Bot will keep running and retry through scheduled jobs. Reason: %s",
            exc,
        )

    poll_sender = PollSender(
        bot=bot,
        sheets_client=sheets_client,
        chat_id=settings.telegram_chat_id,
        timezone_name=settings.timezone,
    )
    dispatcher.include_router(create_command_router(settings, poll_sender, logger))

    quiz_scheduler = QuizScheduler(
        poll_sender=poll_sender,
        timezone_name=settings.timezone,
    )

    scheduler = quiz_scheduler.create_scheduler()
    scheduler.start()

    try:
        logger.info("Bot polling started")
        await dispatcher.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()
        logger.info("Bot stopped")


if __name__ == "__main__":
    asyncio.run(main())
