from __future__ import annotations

import json
import re
from pathlib import Path


USERNAME_RE = re.compile(r"^@?[A-Za-z0-9_]{5,32}$")


class MentionStoreError(RuntimeError):
    """Raised when the mention storage file cannot be read or written."""


class MentionStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def list_mentions(self) -> list[str]:
        if not self._path.exists():
            return []

        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise MentionStoreError(f"Cannot read mentions file {self._path}.") from exc

        if not isinstance(data, list):
            raise MentionStoreError(f"Mentions file {self._path} must contain a JSON list.")

        mentions: list[str] = []
        seen: set[str] = set()
        for item in data:
            if not isinstance(item, str):
                continue
            mention = normalize_username(item)
            key = mention.lower()
            if key not in seen:
                mentions.append(mention)
                seen.add(key)
        return mentions

    def add(self, username: str) -> tuple[bool, list[str]]:
        mention = normalize_username(username)
        mentions = self.list_mentions()
        if mention.lower() in {item.lower() for item in mentions}:
            return False, mentions

        mentions.append(mention)
        self._write_mentions(mentions)
        return True, mentions

    def remove(self, username: str) -> tuple[bool, list[str]]:
        mention = normalize_username(username)
        mentions = self.list_mentions()
        filtered = [item for item in mentions if item.lower() != mention.lower()]
        if len(filtered) == len(mentions):
            return False, mentions

        self._write_mentions(filtered)
        return True, filtered

    def _write_mentions(self, mentions: list[str]) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(mentions, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            raise MentionStoreError(f"Cannot write mentions file {self._path}.") from exc


def normalize_username(username: str) -> str:
    value = username.strip()
    if not USERNAME_RE.fullmatch(value):
        raise ValueError("Expected a Telegram username like @username.")
    if not value.startswith("@"):
        value = f"@{value}"
    return value
