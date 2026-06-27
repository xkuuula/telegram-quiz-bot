from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import asyncio
import logging
import random
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter

from google_sheets import GoogleSheetsClient, SheetQuestion


LOGGER = logging.getLogger(__name__)

TELEGRAM_RETRY_ATTEMPTS = 3
TELEGRAM_REQUEST_TIMEOUT_SECONDS = 30


@dataclass(frozen=True, slots=True)
class PreparedPoll:
    question: str
    options: list[str]
    correct_option_id: int
    explanation: str | None


class PollSender:
    def __init__(
        self,
        bot: Bot,
        sheets_client: GoogleSheetsClient,
        chat_id: int | str,
        timezone_name: str,
    ) -> None:
        self._bot = bot
        self._sheets_client = sheets_client
        self._chat_id = chat_id
        self._timezone = ZoneInfo(timezone_name)
        self._random = random.SystemRandom()
        self._send_lock = asyncio.Lock()

    async def send_next_poll(self, scheduled_at: datetime | None = None) -> bool:
        async with self._send_lock:
            question = await asyncio.to_thread(self._sheets_client.get_next_unsent_question)
            if question is None:
                return False

            poll = self._prepare_poll(question)
            LOGGER.info(
                "Sending quiz poll from row %s to chat %s",
                question.row_number,
                self._chat_id,
            )

            await self._send_poll_with_retry(poll)

            sent_at = scheduled_at or datetime.now(self._timezone)
            await asyncio.to_thread(self._sheets_client.mark_sent, question.row_number, sent_at)
            LOGGER.info("Poll from row %s was sent successfully", question.row_number)
            return True

    async def _send_poll_with_retry(self, poll: PreparedPoll) -> None:
        for attempt in range(1, TELEGRAM_RETRY_ATTEMPTS + 1):
            try:
                await self._bot.send_poll(
                    chat_id=self._chat_id,
                    question=poll.question,
                    options=poll.options,
                    type="quiz",
                    correct_option_id=poll.correct_option_id,
                    explanation=poll.explanation,
                    is_anonymous=False,
                    request_timeout=TELEGRAM_REQUEST_TIMEOUT_SECONDS,
                )
                return
            except TelegramRetryAfter as exc:
                if attempt >= TELEGRAM_RETRY_ATTEMPTS:
                    raise

                delay = exc.retry_after + 1
                LOGGER.warning(
                    "Telegram flood limit hit. Retrying poll send in %s seconds.",
                    delay,
                )
                await asyncio.sleep(delay)

    def _prepare_poll(self, question: SheetQuestion) -> PreparedPoll:
        options = question.options.copy()
        self._random.shuffle(options)
        correct_option_id = options.index(question.correct_answer)

        LOGGER.info(
            "Options for row %s were shuffled; correct option index is %s",
            question.row_number,
            correct_option_id,
        )

        return PreparedPoll(
            question=question.question,
            options=options,
            correct_option_id=correct_option_id,
            explanation=question.explanation or None,
        )
