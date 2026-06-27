from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import asyncio
import logging
import random
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramMigrateToChat,
    TelegramRetryAfter,
)
from aiogram.types import Message

from google_sheets import GoogleSheetsClient, SheetQuestion
from mentions import MentionStore
from pinned_poll import PinnedPollStore, PinnedPollStoreError


LOGGER = logging.getLogger(__name__)

TELEGRAM_RETRY_ATTEMPTS = 3
TELEGRAM_REQUEST_TIMEOUT_SECONDS = 30
TELEGRAM_MESSAGE_LIMIT = 4096


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
        mention_store: MentionStore | None = None,
        pinned_poll_store: PinnedPollStore | None = None,
    ) -> None:
        self._bot = bot
        self._sheets_client = sheets_client
        self._chat_id = chat_id
        self._timezone = ZoneInfo(timezone_name)
        self._random = random.SystemRandom()
        self._send_lock = asyncio.Lock()
        self._mention_store = mention_store
        self._pinned_poll_store = pinned_poll_store

    def set_chat_id(self, chat_id: int | str) -> None:
        if self._chat_id == chat_id:
            return

        LOGGER.warning("Poll target chat changed from %s to %s.", self._chat_id, chat_id)
        self._chat_id = chat_id

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

            await self._send_mentions_with_retry()
            poll_message = await self._send_poll_with_retry(poll)
            await self._pin_poll_with_retry(poll_message.message_id)

            sent_at = scheduled_at or datetime.now(self._timezone)
            await asyncio.to_thread(self._sheets_client.mark_sent, question.row_number, sent_at)
            LOGGER.info("Poll from row %s was sent successfully", question.row_number)
            return True

    async def _send_poll_with_retry(self, poll: PreparedPoll) -> Message:
        for attempt in range(1, TELEGRAM_RETRY_ATTEMPTS + 1):
            try:
                return await self._bot.send_poll(
                    chat_id=self._chat_id,
                    question=poll.question,
                    options=poll.options,
                    type="quiz",
                    correct_option_id=poll.correct_option_id,
                    explanation=poll.explanation,
                    is_anonymous=False,
                    request_timeout=TELEGRAM_REQUEST_TIMEOUT_SECONDS,
                )
            except TelegramRetryAfter as exc:
                if attempt >= TELEGRAM_RETRY_ATTEMPTS:
                    raise

                delay = exc.retry_after + 1
                LOGGER.warning(
                    "Telegram flood limit hit. Retrying poll send in %s seconds.",
                    delay,
                )
                await asyncio.sleep(delay)
            except TelegramMigrateToChat as exc:
                self._handle_chat_migration(exc)

        raise RuntimeError("Telegram poll send retry loop ended unexpectedly.")

    async def _pin_poll_with_retry(self, message_id: int) -> None:
        if self._pinned_poll_store is None:
            return

        try:
            previous_message_id = self._pinned_poll_store.get_message_id()
        except PinnedPollStoreError:
            LOGGER.exception("Failed to read pinned poll state")
            previous_message_id = None

        pinned = await self._pin_message_with_retry(message_id)
        if not pinned:
            return

        try:
            self._pinned_poll_store.set_message_id(message_id)
        except PinnedPollStoreError:
            LOGGER.exception("Failed to save pinned poll state")

        if previous_message_id is not None and previous_message_id != message_id:
            await self._unpin_message(previous_message_id)

    async def _pin_message_with_retry(self, message_id: int) -> bool:
        for attempt in range(1, TELEGRAM_RETRY_ATTEMPTS + 1):
            try:
                await self._bot.pin_chat_message(
                    chat_id=self._chat_id,
                    message_id=message_id,
                    disable_notification=True,
                    request_timeout=TELEGRAM_REQUEST_TIMEOUT_SECONDS,
                )
                return True
            except TelegramRetryAfter as exc:
                if attempt >= TELEGRAM_RETRY_ATTEMPTS:
                    LOGGER.warning("Telegram flood limit hit. Poll pin was skipped: %s", exc)
                    return False

                delay = exc.retry_after + 1
                LOGGER.warning(
                    "Telegram flood limit hit. Retrying poll pin in %s seconds.",
                    delay,
                )
                await asyncio.sleep(delay)
            except TelegramMigrateToChat as exc:
                self._handle_chat_migration(exc)
            except (TelegramBadRequest, TelegramForbiddenError) as exc:
                LOGGER.warning("Telegram rejected poll pin request: %s", exc)
                return False

        return False

    async def _unpin_message(self, message_id: int) -> None:
        try:
            await self._bot.unpin_chat_message(
                chat_id=self._chat_id,
                message_id=message_id,
                request_timeout=TELEGRAM_REQUEST_TIMEOUT_SECONDS,
            )
        except (TelegramBadRequest, TelegramForbiddenError) as exc:
            LOGGER.warning("Telegram rejected previous poll unpin request: %s", exc)
        except TelegramMigrateToChat as exc:
            self._handle_chat_migration(exc)
            await self._unpin_message(message_id)

    async def _send_mentions_with_retry(self) -> None:
        if self._mention_store is None:
            return

        mentions = self._mention_store.list_mentions()
        if not mentions:
            return

        for text in _split_mentions(mentions):
            await self._send_message_with_retry(text)

    async def _send_message_with_retry(self, text: str) -> None:
        for attempt in range(1, TELEGRAM_RETRY_ATTEMPTS + 1):
            try:
                await self._bot.send_message(
                    chat_id=self._chat_id,
                    text=text,
                    request_timeout=TELEGRAM_REQUEST_TIMEOUT_SECONDS,
                )
                return
            except TelegramRetryAfter as exc:
                if attempt >= TELEGRAM_RETRY_ATTEMPTS:
                    raise

                delay = exc.retry_after + 1
                LOGGER.warning(
                    "Telegram flood limit hit. Retrying mention send in %s seconds.",
                    delay,
                )
                await asyncio.sleep(delay)
            except TelegramMigrateToChat as exc:
                self._handle_chat_migration(exc)

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

    def _handle_chat_migration(self, exc: TelegramMigrateToChat) -> None:
        old_chat_id = self._chat_id
        self.set_chat_id(exc.migrate_to_chat_id)
        LOGGER.warning(
            "Telegram chat migrated from %s to %s. Update CHAT_ID in .env to %s.",
            old_chat_id,
            self._chat_id,
            self._chat_id,
        )


def _split_mentions(mentions: list[str]) -> list[str]:
    messages: list[str] = []
    current = ""
    for mention in mentions:
        candidate = mention if not current else f"{current} {mention}"
        if len(candidate) <= TELEGRAM_MESSAGE_LIMIT:
            current = candidate
            continue

        if current:
            messages.append(current)
        current = mention

    if current:
        messages.append(current)
    return messages
