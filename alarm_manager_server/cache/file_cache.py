"""JSON file cache with per-entry TTL (persisted under a mountable directory)."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class FileCache:
    """One JSON file per logical cache type; TTL checked on read via saved_at timestamp."""

    def __init__(self, directory: str | Path, *, enabled: bool = True) -> None:
        self.enabled = enabled
        self.directory = Path(directory).expanduser()
        if self.enabled:
            self.directory.mkdir(parents=True, exist_ok=True)

    def path_for(self, name: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
        return self.directory / f"{safe}.json"

    def get(self, name: str, ttl_sec: int) -> Any | None:
        if not self.enabled or ttl_sec <= 0:
            return None
        path = self.path_for(name)
        if not path.is_file():
            return None
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("cache read failed for %s: %s", name, exc)
            return None
        saved_at = envelope.get("saved_at")
        if not isinstance(saved_at, (int, float)):
            return None
        if time.time() - float(saved_at) > ttl_sec:
            return None
        return envelope.get("payload")

    def set(self, name: str, payload: Any) -> None:
        if not self.enabled:
            return
        path = self.path_for(name)
        envelope = {"saved_at": time.time(), "payload": payload}
        try:
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:
            logger.warning("cache write failed for %s: %s", name, exc)

    def invalidate(self, name: str) -> None:
        if not self.enabled:
            return
        try:
            self.path_for(name).unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("cache invalidate failed for %s: %s", name, exc)

    def clear_all(self) -> None:
        if not self.enabled or not self.directory.is_dir():
            return
        for path in self.directory.glob("*.json"):
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("cache clear failed for %s: %s", path, exc)
