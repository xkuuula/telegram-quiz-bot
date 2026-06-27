from __future__ import annotations

from dataclasses import dataclass
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from google_sheets import GoogleSheetsError
from poll_sender import PollSender


LOGGER = logging.getLogger(__name__)

SCHEDULE_HOURS = (13, 20)


@dataclass(slots=True)
class QuizScheduler:
    poll_sender: PollSender
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

        return scheduler

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
