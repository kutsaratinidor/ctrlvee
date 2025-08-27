import os
import time
import threading
import logging
from typing import Iterable, Set, Optional, List, Callable

from ..config import Config


MEDIA_EXTENSIONS = {
    ".mp4", ".mkv", ".avi", ".mov", ".m4v", ".wmv", ".flv", ".mpg", ".mpeg",
    ".mp3", ".flac", ".aac", ".wav", ".ogg"
}


class WatchFolderService:
    def get_total_media_size(self) -> int:
        """Return the cached total size in bytes of all media files in the watched folders."""
        return getattr(self, '_cached_media_size', 0)

    def _update_media_size_cache(self):
        total = 0
        for folder in self.folders:
            if not os.path.isdir(folder):
                continue
            for path in self._iter_media_files(folder):
                try:
                    total += os.path.getsize(path)
                except Exception:
                    continue
        self._cached_media_size = total
    """Poll-based watch folder service that adds new media files to VLC playlist."""

    def __init__(self, vlc_controller, folders: Optional[List[str]] = None, scan_interval: Optional[int] = None):
        self.vlc = vlc_controller
        self.folders = folders if folders is not None else Config.WATCH_FOLDERS
        self.scan_interval = scan_interval if scan_interval is not None else Config.WATCH_SCAN_INTERVAL
        self.logger = logging.getLogger(__name__)
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._seen: Set[str] = set()
        self._notifier: Optional[Callable[[List[str]], None]] = None
        self._cached_media_size = 0
        self._update_media_size_cache()

    def set_notifier(self, notifier: Callable[[List[str]], None]):
        """Set a callback that will be called with a list of successfully enqueued file paths.

        The callback MUST be thread-safe; it's invoked from the watch thread.
        """
        self._notifier = notifier

    def start(self):
        if not self.folders:
            self.logger.info("WatchFolderService disabled (no WATCH_FOLDERS configured)")
            return False
        if self._thread and self._thread.is_alive():
            return True
        self.logger.info(f"Starting WatchFolderService for {len(self.folders)} folder(s) with interval {self.scan_interval}s")
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="WatchFolderService", daemon=True)
        self._thread.start()
        return True

    def stop(self, timeout: float = 5.0):
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def _run_loop(self):
        # Initial scan
        self.logger.info("Running initial watch-folder scan...")
        self._scan_all(add_to_playlist=Config.WATCH_ENQUEUE_ON_START)
        while not self._stop_event.is_set():
            try:
                self._scan_all(add_to_playlist=True)
            except Exception as e:
                self.logger.error(f"Watch scan error: {e}")
            # Sleep with small checks to allow responsive stop
            slept = 0
            while not self._stop_event.is_set() and slept < self.scan_interval:
                time.sleep(0.5)
                slept += 0.5

    def _iter_media_files(self, base: str) -> Iterable[str]:
        for root, dirs, files in os.walk(base):
            # Skip hidden directories
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            for f in files:
                if f.startswith('.'):
                    continue
                ext = os.path.splitext(f)[1].lower()
                if ext in MEDIA_EXTENSIONS:
                    yield os.path.join(root, f)

    def _stable_file(self, path: str, min_age: float = 2.0) -> bool:
        """Return True if file size hasn't changed for min_age seconds."""
        try:
            s1 = os.stat(path)
            time.sleep(min_age)
            s2 = os.stat(path)
            return s1.st_size == s2.st_size
        except FileNotFoundError:
            return False
        except Exception as e:
            self.logger.debug(f"stat failed for {path}: {e}")
            return False

    def _scan_all(self, add_to_playlist: bool):
        # Reload .env to pick up new folders
        try:
            from dotenv import load_dotenv
            load_dotenv(override=True)
            # Parse the latest WATCH_FOLDERS from the environment
            env_val = os.environ.get('WATCH_FOLDERS', '')
            parsed_folders = [p.strip() for p in env_val.split(',') if p.strip()]
            # No log for every reload; only log when new folders are added
        except Exception:
            self.logger.warning("Failed to reload .env for hot watch folder changes.")
            parsed_folders = self.folders
        # Hot-reload folders from env
        current_folders = set(self.folders)
        config_folders = set(parsed_folders)
        new_folders = config_folders - current_folders
        if new_folders:
            self.logger.info(f"Hot-loading new watch folders: {', '.join(new_folders)}")
            self.folders.extend(f for f in new_folders if f not in self.folders)

        new_files = []
        for folder in self.folders:
            if not os.path.isdir(folder):
                self.logger.warning(f"Watch folder not found or not a directory: {folder}")
                continue
            for path in self._iter_media_files(folder):
                if path in self._seen:
                    continue
                self._seen.add(path)
                new_files.append(path)

        if not new_files:
            return

        self.logger.info(f"Discovered {len(new_files)} new media file(s):")
        for nf in new_files:
            self.logger.info(f" - {nf}")
        if not add_to_playlist:
            self.logger.info("Initial discovery only (not enqueuing this pass)")
            return

        # Enqueue each new file by path
        enqueued: List[str] = []
        total = len(new_files)
        for idx, path in enumerate(new_files, 1):
            try:
                if not self._stable_file(path):
                    self.logger.info(f"Skipping (unstable/moving): {idx}/{total} {path}")
                    continue
                self.logger.info(f"Enqueuing via VLC: {idx}/{total} {path}")
                ok = self.vlc.enqueue_path(path)
                if ok:
                    self.logger.info(f"Enqueued: {idx}/{total} {path}")
                    enqueued.append(path)
                else:
                    self.logger.warning(f"Failed to enqueue: {idx}/{total} {path}")
            except Exception as e:
                self.logger.error(f"Error enqueuing {idx}/{total} {path}: {e}")

        # Update cached media size after each scan
        self._update_media_size_cache()

        # Notify if any were added
        if enqueued and self._notifier:
            try:
                self._notifier(enqueued)
            except Exception as e:
                self.logger.error(f"Notifier error: {e}")
