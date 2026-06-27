from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv
import os


class ConfigError(RuntimeError):
    """Raised when a required environment variable is missing or invalid."""


@dataclass(frozen=True, slots=True)
class Settings:
    bot_token: str
    chat_id: str
    send_now_user_id: int | None
    mention_admin_user_id: int | None
    mentions_path: Path
    pinned_poll_path: Path
    google_sheet_id: str | None
    google_sheet_name: str
    timezone: str
    credentials_path: Path
    google_credentials_json: str | None
    google_credentials_base64: str | None

    @property
    def zone_info(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ConfigError(
                f"Invalid TIMEZONE '{self.timezone}'. Use an IANA timezone, for example Europe/Moscow."
            ) from exc

    @property
    def telegram_chat_id(self) -> int | str:
        value = self.chat_id.strip()
        if value.lstrip("-").isdigit():
            return int(value)
        return value


def _get_required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigError(f"Environment variable {name} is required.")
    return value


def load_settings(env_file: str | Path = ".env") -> Settings:
    load_dotenv(env_file)

    project_dir = Path(__file__).resolve().parent
    credentials_path = project_dir / "credentials.json"
    send_now_user_id = _get_optional_int("SEND_NOW_USER_ID")
    mention_admin_user_id = _get_optional_int("MENTION_ADMIN_USER_ID")
    mentions_path_raw = os.getenv("MENTIONS_FILE", "").strip()
    mentions_path = Path(mentions_path_raw) if mentions_path_raw else project_dir / "mentions.json"
    if not mentions_path.is_absolute():
        mentions_path = project_dir / mentions_path
    pinned_poll_path_raw = os.getenv("PINNED_POLL_FILE", "").strip()
    pinned_poll_path = (
        Path(pinned_poll_path_raw) if pinned_poll_path_raw else project_dir / "pinned_poll.json"
    )
    if not pinned_poll_path.is_absolute():
        pinned_poll_path = project_dir / pinned_poll_path
    google_sheet_id = os.getenv("GOOGLE_SHEET_ID", "").strip() or None
    google_sheet_name = os.getenv("GOOGLE_SHEET_NAME", "").strip() or None
    google_credentials_json = os.getenv("GOOGLE_CREDENTIALS_JSON", "").strip() or None
    google_credentials_base64 = os.getenv("GOOGLE_CREDENTIALS_BASE64", "").strip() or None

    if not google_sheet_id and not google_sheet_name:
        raise ConfigError("Environment variable GOOGLE_SHEET_ID or GOOGLE_SHEET_NAME is required.")

    return Settings(
        bot_token=_get_required_env("BOT_TOKEN"),
        chat_id=_get_required_env("CHAT_ID"),
        send_now_user_id=send_now_user_id,
        mention_admin_user_id=mention_admin_user_id or send_now_user_id,
        mentions_path=mentions_path,
        pinned_poll_path=pinned_poll_path,
        google_sheet_id=google_sheet_id,
        google_sheet_name=google_sheet_name or "",
        timezone=_get_required_env("TIMEZONE"),
        credentials_path=credentials_path,
        google_credentials_json=google_credentials_json,
        google_credentials_base64=google_credentials_base64,
    )


def _get_optional_int(name: str) -> int | None:
    raw_value = os.getenv(name, "").strip()
    if raw_value:
        try:
            return int(raw_value)
        except ValueError as exc:
            raise ConfigError(f"{name} must be a valid integer.") from exc
    return None
