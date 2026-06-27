from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import asyncio
import logging
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from google_sheets import GoogleSheetsClient, GoogleSheetsError
from poll_sender import PollSender


LOGGER = logging.getLogger(__name__)

SCHEDULE_HOURS = (13, 20)


@dataclass(slots=True)
class QuizScheduler:
    poll_sender: PollSender
    sheets_client: GoogleSheetsClient
    timezone_name: str

    def create_scheduler(self) -> AsyncIOScheduler:
        scheduler = AsyncIOScheduler(timezone=self.timezone_name)

        for hour in SCHEDULE_HOURS:
            scheduler.add_job(
                self._run_regular_job,
                trigger=CronTrigger(hour=hour, minute=0, timezone=self.timezone_name),
                id=f"send_quiz_poll_{hour:02d}_00",
                replace_existing=True,
                coalesce=False,
                max_instances=1,
                misfire_grace_time=None,
            )
            LOGGER.info("Scheduled quiz poll job at %02d:00", hour)

        scheduler.add_job(
            self.catch_up_missed_jobs,
            trigger=IntervalTrigger(minutes=5, timezone=self.timezone_name),
            id="catch_up_missed_quiz_polls",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        LOGGER.info("Scheduled missed jobs retry every 5 minutes")

        return scheduler

    async def catch_up_missed_jobs(self) -> None:
        now = datetime.now(ZoneInfo(self.timezone_name))
        try:
            missed_count = await asyncio.to_thread(
                self.sheets_client.count_missed_slots,
                now,
                SCHEDULE_HOURS,
            )
        except GoogleSheetsError as exc:
            LOGGER.warning("Could not check missed scheduled jobs. Will retry later. Reason: %s", exc)
            return
        except Exception:
            LOGGER.exception("Unexpected error while checking missed scheduled jobs")
            return

        if missed_count <= 0:
            return

        LOGGER.info("Processing %s missed scheduled jobs", missed_count)
        for number in range(1, missed_count + 1):
            LOGGER.info("Processing missed job %s of %s", number, missed_count)
            sent = await self._safe_send()
            if not sent:
                LOGGER.info("Stopped missed job processing because there are no questions to send")
                break

    async def _run_regular_job(self) -> None:
        LOGGER.info("Regular scheduled job started")
        await self._safe_send()

    async def _safe_send(self) -> bool:
        try:
            return await self.poll_sender.send_next_poll()
        except GoogleSheetsError as exc:
            LOGGER.warning(
                "Scheduled poll sending failed because Google Sheets is unavailable. "
                "The question was not marked as SENT. Reason: %s",
                exc,
            )
            return False
        except Exception:
            LOGGER.exception(
                "Scheduled poll sending failed. The question was not marked as SENT."
            )
            return False
