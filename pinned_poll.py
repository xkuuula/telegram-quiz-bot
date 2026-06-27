from __future__ import annotations

import json
from pathlib import Path


class PinnedPollStoreError(RuntimeError):
    """Raised when the pinned poll state cannot be read or written."""


class PinnedPollStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def get_message_id(self) -> int | None:
        if not self._path.exists():
            return None

        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PinnedPollStoreError(f"Cannot read pinned poll file {self._path}.") from exc

        if not isinstance(data, dict):
            raise PinnedPollStoreError(f"Pinned poll file {self._path} must contain a JSON object.")

        message_id = data.get("message_id")
        if message_id is None:
            return None
        if not isinstance(message_id, int):
            raise PinnedPollStoreError("Pinned poll message_id must be an integer.")
        return message_id

    def set_message_id(self, message_id: int) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps({"message_id": message_id}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            raise PinnedPollStoreError(f"Cannot write pinned poll file {self._path}.") from exc
