"""
Small TTL-based disk cache.

Exists mainly so we stop re-downloading Sleeper's ~5MB NFL player blob on every
single newspaper generation. Sleeper explicitly asks callers to fetch that file
at most once per day; before this, every click of "Generate" pulled it fresh,
which is a fast track to being rate limited once more than one league is live.

Deliberately boring: JSON on disk, atomic writes, no external dependencies.
If you later move to a multi-process deployment, swap the backend here for
Redis and nothing upstream has to change.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

DEFAULT_CACHE_DIR = Path(os.getenv("COMMISH_CACHE_DIR", ".cache"))

_lock = threading.Lock()


class TTLCache:
    """A JSON file cache where every entry carries its own expiry."""

    def __init__(self, cache_dir: Path | str = DEFAULT_CACHE_DIR, namespace: str = "default"):
        self.dir = Path(cache_dir) / namespace
        self.dir.mkdir(parents=True, exist_ok=True)

    # -- internals ---------------------------------------------------------

    def _path(self, key: str) -> Path:
        # Hash so keys can contain slashes, colons, whatever.
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
        return self.dir / f"{digest}.json"

    # -- api ---------------------------------------------------------------

    def get(self, key: str) -> Optional[Any]:
        """Return the cached value, or None if missing or expired."""
        path = self._path(key)
        if not path.exists():
            return None
        try:
            with path.open("r", encoding="utf-8") as f:
                payload = json.load(f)
        except (json.JSONDecodeError, OSError):
            # Corrupt entry -- treat as a miss and let it be overwritten.
            return None

        expires_at = payload.get("expires_at", 0)
        if expires_at and time.time() > expires_at:
            return None
        return payload.get("value")

    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        """Store a value for `ttl_seconds`. Pass ttl_seconds=0 to never expire."""
        path = self._path(key)
        payload = {
            "key": key,
            "cached_at": time.time(),
            "expires_at": (time.time() + ttl_seconds) if ttl_seconds else 0,
            "value": value,
        }
        # Atomic write: a crash mid-write must not leave a truncated file that
        # every later read has to defend against.
        with _lock:
            fd, tmp = tempfile.mkstemp(dir=str(self.dir), suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(payload, f)
                os.replace(tmp, path)
            except Exception:
                if os.path.exists(tmp):
                    os.unlink(tmp)
                raise

    def get_or_fetch(self, key: str, ttl_seconds: int, fetch: Callable[[], Any]) -> Any:
        """Return the cached value, or call `fetch()`, store, and return it."""
        hit = self.get(key)
        if hit is not None:
            return hit
        value = fetch()
        # Don't cache empty responses -- an API blip shouldn't poison the cache
        # for a full day.
        if value:
            self.set(key, value, ttl_seconds)
        return value

    def invalidate(self, key: str) -> None:
        path = self._path(key)
        if path.exists():
            try:
                path.unlink()
            except OSError:
                pass

    def clear(self) -> int:
        """Delete every entry in this namespace. Returns the count removed."""
        removed = 0
        for p in self.dir.glob("*.json"):
            try:
                p.unlink()
                removed += 1
            except OSError:
                pass
        return removed


# Common TTLs, named so call sites read clearly.
TTL_PLAYER_INDEX = 24 * 60 * 60   # Sleeper asks for once per day, max
TTL_PROJECTIONS = 6 * 60 * 60     # shift during the week, settle after
TTL_LEAGUE_META = 60 * 60         # names/settings rarely change mid-season
TTL_LIVE_SCORES = 60              # in-progress games
TTL_FINAL_SCORES = 7 * 24 * 60 * 60   # a completed week never changes again
