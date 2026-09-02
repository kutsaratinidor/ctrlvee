import os
import json
import asyncio
import threading
import logging
from datetime import datetime, timezone
from typing import Callable, List, Optional

from ..config import Config
from .overseerr_service import REQUEST_STATUS_DECLINED, REQUEST_STATUS_FAILED

# Statuses that mean polling has nothing left to check.
TERMINAL_STATUSES = ("available", "declined", "failed", "removed")
# Of those, the ones that should NOT block a fresh request for the same title
# (declined/failed/removed all mean there's no live request on Seerr anymore).
RETRYABLE_STATUSES = ("declined", "failed", "removed")


class MovieRequestTracker:
    def __init__(self, overseerr_service, store_file: Optional[str] = None, poll_interval: Optional[int] = None):
        self.overseerr = overseerr_service
        self.store_file = store_file if store_file is not None else Config.REQUEST_STORE_FILE
        if self.store_file and not os.path.isabs(self.store_file):
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            self.store_file = os.path.join(project_root, self.store_file)
        self.poll_interval = poll_interval if poll_interval is not None else Config.REQUEST_POLL_INTERVAL
        self.logger = logging.getLogger(__name__)
        self._lock = threading.Lock()
        self._records: List[dict] = self._load()
        self._notifier: Optional[Callable[[dict], None]] = None
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def _load(self) -> List[dict]:
        if not self.store_file or not os.path.isfile(self.store_file):
            return []
        try:
            with open(self.store_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except Exception as e:
            self.logger.error(f"Failed to load movie request store '{self.store_file}': {e}")
            return []

    def _save(self) -> None:
        try:
            with open(self.store_file, "w", encoding="utf-8") as f:
                json.dump(self._records, f, indent=2)
        except Exception as e:
            self.logger.error(f"Failed to save movie request store '{self.store_file}': {e}")

    def add_request(self, record: dict) -> None:
        """Append a new tracked request and persist immediately."""
        with self._lock:
            self._records.append(record)
            self._save()

    def get_requests_for_user(self, user_id: int) -> List[dict]:
        """Return the given user's tracked records, most recently requested first."""
        with self._lock:
            matches = [dict(r) for r in self._records if r.get("requested_by_id") == user_id]
        return sorted(matches, key=lambda r: r.get("requested_at") or "", reverse=True)

    def clear_terminal_for_user(self, user_id: int) -> int:
        """Remove this user's declined/failed/removed records. Returns how many were removed."""
        with self._lock:
            before = len(self._records)
            self._records = [
                r for r in self._records
                if not (r.get("requested_by_id") == user_id and r.get("status") in RETRYABLE_STATUSES)
            ]
            removed = before - len(self._records)
            if removed:
                self._save()
        return removed

    def find_request_by_tmdb_id(self, tmdb_id: int) -> Optional[dict]:
        """Return the tracked record for this movie, if one already exists (any requester)."""
        with self._lock:
            for r in self._records:
                if r.get("tmdb_id") == tmdb_id:
                    return dict(r)
        return None

    def set_notifier(self, notifier: Callable[[dict], None]) -> None:
        """Register a callback invoked (from the polling thread) with the record dict
        when a request transitions to available. Callback must be thread-safe."""
        self._notifier = notifier

    def start(self) -> bool:
        if not self.store_file:
            self.logger.info("MovieRequestTracker disabled (no REQUEST_STORE_FILE configured)")
            return False
        if self._thread and self._thread.is_alive():
            return True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="MovieRequestTracker", daemon=True)
        self._thread.start()
        return True

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.poll_once()
            except Exception as e:
                self.logger.error(f"MovieRequestTracker poll iteration failed: {e}")
            self._stop_event.wait(self.poll_interval)

    def poll_once(self) -> None:
        """Check every unresolved record once against Overseerr. Synchronous
        (bridges into a short-lived event loop per check) so it can be called
        directly from a plain thread or from a verification script."""
        with self._lock:
            pending = [r for r in self._records if r.get("status") not in TERMINAL_STATUSES]

        changed = False
        for record in pending:
            tmdb_id = record.get("tmdb_id")
            request_id = record.get("overseerr_request_id")
            if request_id is None:
                self.logger.warning(f"Record for tmdb_id={tmdb_id} has no overseerr_request_id; skipping status check")
                continue
            try:
                result = asyncio.run(self.overseerr.get_request_status(request_id))
            except Exception as e:
                self.logger.error(f"Status check failed for request_id={request_id} (tmdb_id={tmdb_id}): {e}")
                continue

            if not result.get("success"):
                self.logger.warning(f"Status check error for request_id={request_id} (tmdb_id={tmdb_id}): {result.get('error')}")
                continue

            if not result.get("found", True):
                # Request was deleted on the Seerr side. Quiet update: no notification,
                # and this no longer blocks a future re-request for the same title.
                with self._lock:
                    record["status"] = "removed"
                changed = True
                continue

            # Availability wins regardless of the request's own approval status: Overseerr
            # can leave a request marked Declined/Failed even after its media later becomes
            # available through another path (e.g. a manual Radarr import), and the user
            # getting notified matters more than that stale approval-status field.
            if result.get("available"):
                with self._lock:
                    record["status"] = "available"
                    record["notified_at"] = datetime.now(timezone.utc).isoformat()
                changed = True
                if self._notifier:
                    try:
                        self._notifier(record)
                    except Exception as e:
                        self.logger.error(f"Notifier callback failed for tmdb_id={tmdb_id}: {e}")
                continue

            request_status = result.get("request_status")
            if request_status in (REQUEST_STATUS_DECLINED, REQUEST_STATUS_FAILED):
                with self._lock:
                    record["status"] = "declined" if request_status == REQUEST_STATUS_DECLINED else "failed"
                changed = True
                continue

        if changed:
            with self._lock:
                self._save()
