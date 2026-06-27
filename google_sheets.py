from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
import json
import logging
from pathlib import Path
import threading
import time
from typing import TypeVar

import gspread
from gspread.exceptions import APIError
from google.oauth2.service_account import Credentials
from requests.exceptions import RequestException


LOGGER = logging.getLogger(__name__)

SCOPES = ("https://www.googleapis.com/auth/spreadsheets",)
NOT_SENT = "NOT_SENT"
SENT = "SENT"
STATUS_COLUMN = 4
SENT_AT_COLUMN = 5
MIN_COLUMNS = 5
REQUEST_TIMEOUT_SECONDS = 20
API_RETRY_ATTEMPTS = 3
API_RETRY_BASE_DELAY_SECONDS = 2

T = TypeVar("T")


class GoogleSheetsError(RuntimeError):
    """Raised when Google Sheets cannot be accessed or updated."""


class QuestionValidationError(ValueError):
    """Raised when a row cannot be converted to a valid Telegram quiz poll."""


@dataclass(frozen=True, slots=True)
class SheetQuestion:
    row_number: int
    question: str
    options: list[str]
    correct_answer: str
    explanation: str


class GoogleSheetsClient:
    def __init__(
        self,
        credentials_path: Path,
        credentials_json: str | None = None,
        credentials_base64: str | None = None,
        sheet_id: str | None = None,
        sheet_name: str | None = None,
    ) -> None:
        if not sheet_id and not sheet_name:
            raise GoogleSheetsError("Either sheet_id or sheet_name must be provided.")

        self._sheet_id = sheet_id
        self._sheet_name = sheet_name
        self._credentials_path = credentials_path
        self._credentials_json = credentials_json
        self._credentials_base64 = credentials_base64
        self._worksheet: gspread.Worksheet | None = None
        self._lock = threading.RLock()

    def connect(self) -> None:
        with self._lock:
            LOGGER.info("Connecting to Google Sheets")
            credentials = self._load_credentials()
            client = gspread.authorize(credentials)
            client.set_timeout(REQUEST_TIMEOUT_SECONDS)

            def open_worksheet() -> gspread.Worksheet:
                if self._sheet_id:
                    spreadsheet = client.open_by_key(self._sheet_id)
                else:
                    spreadsheet = client.open(self._sheet_name)
                return spreadsheet.sheet1

            self._worksheet = self._run_with_retry("connect", open_worksheet)
            LOGGER.info("Connected to Google Sheets document %s", self._describe_target())

    def _load_credentials(self) -> Credentials:
        if self._credentials_base64:
            try:
                decoded_credentials = base64.b64decode(self._credentials_base64).decode("utf-8")
                credentials_info = json.loads(decoded_credentials)
            except (ValueError, json.JSONDecodeError) as exc:
                raise GoogleSheetsError("GOOGLE_CREDENTIALS_BASE64 contains invalid credentials JSON.") from exc

            return Credentials.from_service_account_info(credentials_info, scopes=SCOPES)

        if self._credentials_json:
            try:
                credentials_info = json.loads(self._credentials_json)
            except json.JSONDecodeError as exc:
                raise GoogleSheetsError("GOOGLE_CREDENTIALS_JSON contains invalid JSON.") from exc

            return Credentials.from_service_account_info(credentials_info, scopes=SCOPES)

        if not self._credentials_path.exists():
            raise GoogleSheetsError(
                f"Credentials file was not found: {self._credentials_path}. "
                "Place Google Service Account credentials.json in the project directory "
                "or set GOOGLE_CREDENTIALS_BASE64 or GOOGLE_CREDENTIALS_JSON."
            )

        return Credentials.from_service_account_file(self._credentials_path, scopes=SCOPES)

    @property
    def worksheet(self) -> gspread.Worksheet:
        if self._worksheet is None:
            self.connect()
        if self._worksheet is None:
            raise GoogleSheetsError("Google Sheets worksheet is not initialized.")
        return self._worksheet

    def reconnect(self) -> None:
        LOGGER.info("Reconnecting to Google Sheets")
        with self._lock:
            self._worksheet = None
        self.connect()

    def ensure_service_columns(self) -> list[list[str]]:
        with self._lock:
            worksheet = self.worksheet

            if worksheet.col_count < MIN_COLUMNS:
                self._run_with_retry(
                    "resize worksheet",
                    lambda: worksheet.resize(rows=worksheet.row_count, cols=MIN_COLUMNS),
                )
                LOGGER.info("Google Sheets service columns D and E were created")

            rows = self._run_with_retry("read worksheet values", worksheet.get_all_values)
            status_updates: list[dict[str, list[list[str]] | str]] = []

            for index, row in enumerate(rows, start=1):
                if not self._row_has_question_data(row):
                    continue

                status = self._cell(row, STATUS_COLUMN).strip()
                if not status:
                    status_updates.append({"range": f"D{index}", "values": [[NOT_SENT]]})
                    self._set_local_cell(row, STATUS_COLUMN, NOT_SENT)

            if status_updates:
                self._run_with_retry(
                    "initialize empty statuses",
                    lambda: worksheet.batch_update(status_updates),
                )
                LOGGER.info("Initialized %s empty status cells with NOT_SENT", len(status_updates))

            return rows

    def get_next_unsent_question(self) -> SheetQuestion | None:
        LOGGER.info("Searching for the next NOT_SENT question")
        rows = self.ensure_service_columns()

        for index, row in enumerate(rows, start=1):
            status = self._cell(row, STATUS_COLUMN).strip().upper()
            if status != NOT_SENT:
                continue

            try:
                return self._parse_question_row(index, row)
            except QuestionValidationError as exc:
                LOGGER.error("Invalid row %s skipped: %s", index, exc)

        LOGGER.info("No valid NOT_SENT questions found")
        return None

    def mark_sent(self, row_number: int, sent_at: datetime) -> None:
        timestamp = sent_at.isoformat(timespec="seconds")
        LOGGER.info("Changing row %s status to SENT at %s", row_number, timestamp)

        with self._lock:
            worksheet = self.worksheet
            self._run_with_retry(
                "mark question as sent",
                lambda: worksheet.batch_update(
                    [
                        {
                            "range": f"D{row_number}:E{row_number}",
                            "values": [[SENT, timestamp]],
                        }
                    ]
                ),
            )

    def count_missed_slots(self, now: datetime, schedule_hours: tuple[int, ...]) -> int:
        last_sent_at = self.get_last_sent_at()
        missed = 0

        if last_sent_at is None:
            day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            for hour in schedule_hours:
                slot = day_start.replace(hour=hour)
                if slot <= now:
                    missed += 1
            LOGGER.info("Detected %s missed scheduled jobs for first run today", missed)
            return missed

        current_day = last_sent_at.date()
        while current_day <= now.date():
            for hour in schedule_hours:
                slot = datetime.combine(
                    current_day,
                    datetime.min.time(),
                    tzinfo=now.tzinfo,
                ).replace(hour=hour)
                if last_sent_at < slot <= now:
                    missed += 1
            current_day = current_day.fromordinal(current_day.toordinal() + 1)

        LOGGER.info("Detected %s missed scheduled jobs since %s", missed, last_sent_at)
        return missed

    def get_last_sent_at(self) -> datetime | None:
        rows = self.ensure_service_columns()
        timestamps: list[datetime] = []

        for row in rows:
            status = self._cell(row, STATUS_COLUMN).strip().upper()
            sent_at = self._cell(row, SENT_AT_COLUMN).strip()
            if status != SENT or not sent_at:
                continue

            try:
                timestamps.append(datetime.fromisoformat(sent_at))
            except ValueError:
                LOGGER.error("Invalid sent timestamp skipped: %s", sent_at)

        return max(timestamps) if timestamps else None

    def _run_with_retry(self, operation_name: str, operation: Callable[[], T]) -> T:
        last_error: Exception | None = None

        for attempt in range(1, API_RETRY_ATTEMPTS + 1):
            try:
                return operation()
            except (APIError, RequestException, TimeoutError) as exc:
                last_error = exc
                if attempt >= API_RETRY_ATTEMPTS:
                    break

                delay = API_RETRY_BASE_DELAY_SECONDS * attempt
                LOGGER.warning(
                    "Google Sheets operation '%s' failed on attempt %s/%s. Retrying in %s seconds.",
                    operation_name,
                    attempt,
                    API_RETRY_ATTEMPTS,
                    delay,
                )
                time.sleep(delay)

        raise GoogleSheetsError(self._describe_target(last_error)) from last_error

    @staticmethod
    def _row_has_question_data(row: list[str]) -> bool:
        return any(cell.strip() for cell in row[:3])

    @staticmethod
    def _cell(row: list[str], column_number: int) -> str:
        index = column_number - 1
        if index >= len(row):
            return ""
        return row[index]

    @staticmethod
    def _set_local_cell(row: list[str], column_number: int, value: str) -> None:
        index = column_number - 1
        while len(row) <= index:
            row.append("")
        row[index] = value

    def _parse_question_row(self, row_number: int, row: list[str]) -> SheetQuestion:
        question = self._cell(row, 1).strip()
        raw_options = self._cell(row, 2).strip()
        explanation = self._cell(row, 3).strip()

        if not question:
            raise QuestionValidationError("question is empty")

        options = [option.strip() for option in raw_options.split(",") if option.strip()]
        if len(options) < 2:
            raise QuestionValidationError("at least two answer options are required")
        if len(options) > 10:
            raise QuestionValidationError("Telegram polls support no more than ten answer options")

        correct_answer = options[0]
        if correct_answer not in options:
            raise QuestionValidationError("correct answer does not exist")

        return SheetQuestion(
            row_number=row_number,
            question=question,
            options=options,
            correct_answer=correct_answer,
            explanation=explanation,
        )

    def _describe_target(self, exc: Exception | None = None) -> str:
        if self._sheet_id:
            target = f"spreadsheet id '{self._sheet_id}'"
        else:
            target = f"spreadsheet name '{self._sheet_name}'"

        if exc is None:
            return target

        return f"Could not connect to {target}: {exc}"
