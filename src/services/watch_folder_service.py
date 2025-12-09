import os
import re
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
    def __init__(self, vlc_controller, folders: Optional[List[str]] = None, scan_interval: Optional[int] = None):
        self.vlc = vlc_controller
        base_folders = folders if folders is not None else Config.WATCH_FOLDERS
        # Normalize initial folders to avoid duplicates from trailing slashes, etc.
        self.folders = [os.path.normpath(os.path.abspath(p)) for p in base_folders if p]
        self.scan_interval = scan_interval if scan_interval is not None else Config.WATCH_SCAN_INTERVAL
        self.logger = logging.getLogger(__name__)
        self._stop_event = threading.Event()
        self._thread = None
        self._seen = set()
        # Files being tracked for stability across scans: path -> (size, first_seen_time)
        self._pending = {}
        self._notifier = None
        self._cached_media_size = 0
        # Defer media size calculation; do it in a background thread after start()
        self._size_thread = None
        # Event to signal that the very first scan has completed
        self._initial_scan_done = threading.Event()
        # Track last WATCH_FOLDERS env value for change diagnostics
        self._last_env_val = None
        # Track whether we've completed the very first scan since service start
        self._first_scan_done = False

    # Media size cache helpers
    def get_total_media_size(self) -> int:
        """Return the cached total size in bytes of all media files in the watched folders."""
        return getattr(self, '_cached_media_size', 0)

    def _update_media_size_cache(self):
        """Compute and cache total size of media files across all watch folders."""
        all_files: List[str] = []
        for folder in self.folders:
            if not os.path.isdir(folder):
                continue
            all_files.extend(list(self._iter_media_files(folder)))
        total_files = len(all_files)
        total = 0
        for idx, path in enumerate(all_files, 1):
            try:
                total += os.path.getsize(path)
            except Exception:
                continue
            if idx == 1 or idx % 200 == 0 or idx == total_files:
                self.logger.info(f"Calculating media size: {idx}/{total_files} files processed...")
        self._cached_media_size = total

    def set_notifier(self, notifier: Callable[[List[str], bool], None]):
        """Set a callback that will be called with a list of successfully enqueued file paths.

        The callback MUST be thread-safe; it's invoked from the watch thread.
        The second argument indicates whether this notification is from the initial scan.
        """
        self._notifier = notifier

    def start(self):
        if not self.folders:
            self.logger.info("WatchFolderService disabled (no WATCH_FOLDERS configured)")
            return False
        if self._thread and self._thread.is_alive():
            return True
        self.logger.info(f"Starting WatchFolderService for {len(self.folders)} folder(s) with interval {self.scan_interval}s")
        for f in self.folders:
            self.logger.info(f" - Watching: {f}")
        self._stop_event.clear()
        self._initial_scan_done.clear()
        self._thread = threading.Thread(target=self._run_loop, name="WatchFolderService", daemon=True)
        self._thread.start()
        # Kick off the (potentially long) media size calculation without blocking startup
        self._init_media_size_cache_async()
        return True

    def stop(self, timeout: float = 5.0):
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def _run_loop(self):
        # Initial scan
        self.logger.info("Running initial watch-folder scan...")
        self.logger.info(f"Enqueue on start: {Config.WATCH_ENQUEUE_ON_START}")
        self._scan_all(add_to_playlist=Config.WATCH_ENQUEUE_ON_START)
        # Signal that initial scan has completed
        self._initial_scan_done.set()
        
        # After the first scan, signal to the playback cog that it's complete
        try:
            playback_cog = self.vlc.bot.get_cog('PlaybackCommands')
            if playback_cog:
                playback_cog.signal_initial_scan_complete()
        except Exception as e:
            self.logger.warning(f"Could not signal initial scan completion to Playback cog: {e}")

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
        for root, dirs, files in os.walk(base, followlinks=True):
            # Skip hidden directories
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            for f in files:
                if f.startswith('.'):
                    continue
                ext = os.path.splitext(f)[1].lower()
                if ext in MEDIA_EXTENSIONS:
                    yield os.path.join(root, f)

    def _maybe_mark_stable(self, path: str) -> bool:
        """Track a new file and return True if it is considered stable now.

        Uses a non-blocking approach: a file is considered stable if we've seen
        it in a previous scan with the same size and it has remained unchanged
        for at least Config.WATCH_STABLE_AGE seconds.
        """
        try:
            st = os.stat(path)
            size = st.st_size
            mtime = st.st_mtime
        except Exception:
            # File disappeared or unreadable
            if path in self._pending:
                self._pending.pop(path, None)
            return False

        now = time.time()
        stable_age = max(0.0, float(getattr(Config, 'WATCH_STABLE_AGE', 2)))
        if path not in self._pending:
            # Fast path: if file is already older than stable age, treat as stable immediately
            if (now - mtime) >= stable_age:
                return True
            self._pending[path] = (size, now)
            return False

        prev_size, first_seen = self._pending.get(path, (None, now))
        if prev_size == size and (now - first_seen) >= stable_age:
            # Stable -> cleanup pending entry
            self._pending.pop(path, None)
            return True
        # Update tracking; keep original first_seen if size unchanged, else reset timer
        if prev_size != size:
            self._pending[path] = (size, now)
        return False

    def _scan_all(self, add_to_playlist: bool):
        # Reload .env to pick up new folders
        try:
            from dotenv import load_dotenv
            load_dotenv(override=True)
            env_val = os.environ.get('WATCH_FOLDERS', '') or ''
        except Exception:
            self.logger.warning("Failed to reload .env for hot watch folder changes.")
            env_val = ''

        # Parse WATCH_FOLDERS robustly: allow comma or semicolon separators, strip quotes, normalize paths
        raw_parts = [p for p in re.split(r'[;,]', env_val) if p is not None]
        parsed_folders = []
        for p in raw_parts:
            s = p.strip().strip('"\'')
            if not s:
                continue
            np = os.path.normpath(os.path.abspath(s))
            parsed_folders.append(np)

        # Hot-reload folders from env (only add new ones)
        current_folders = set(os.path.normpath(os.path.abspath(p)) for p in self.folders)
        config_folders = set(parsed_folders)

        # Optional diagnostic when the env value changes
        if env_val and env_val != self._last_env_val:
            self.logger.info(f"WATCH_FOLDERS changed: '{self._last_env_val or ''}' -> '{env_val}'")
            self._last_env_val = env_val

        new_folders = sorted(config_folders - current_folders)
        if new_folders:
            self.logger.info(f"Hot-loading new watch folders: {', '.join(new_folders)}")
            for f in new_folders:
                if f not in self.folders:
                    self.folders.append(f)

        new_files = []
        pending_before = len(self._pending)
        is_first_pass = not self._first_scan_done
        for folder in self.folders:
            if not os.path.isdir(folder):
                self.logger.warning(f"Watch folder not found or not a directory: {folder}")
                continue
            for path in self._iter_media_files(folder):
                if is_first_pass:
                    # On first pass, mark everything as seen; enqueue only if configured
                    if path not in self._seen:
                        self._seen.add(path)
                    if add_to_playlist:
                        new_files.append(path)
                    continue
                if path in self._seen:
                    continue
                # Track stability without blocking the scan thread
                if self._maybe_mark_stable(path):
                    new_files.append(path)
                    if add_to_playlist:
                        self._seen.add(path)

        # First pass completed
        if is_first_pass:
            self._first_scan_done = True

        # Clean up pending entries for files that no longer exist to avoid leaks
        to_delete = [p for p in self._pending.keys() if not os.path.exists(p)]
        for p in to_delete:
            self._pending.pop(p, None)

        if not new_files:
            if pending_before != len(self._pending):
                self.logger.info(f"Watch scan: {len(self._pending)} file(s) pending stability (age>={Config.WATCH_STABLE_AGE}s)")
            return

        self.logger.info(f"Discovered {len(new_files)} new media file(s) (initial={is_first_pass}, enqueue={add_to_playlist}):")
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
                self.logger.info(f"Enqueuing via VLC: {idx}/{total} {path}")
                ok = self.vlc.enqueue_path(path)
                if ok:
                    self.logger.info(f"Enqueued: {idx}/{total} {path}")
                    enqueued.append(path)
                else:
                    self.logger.warning(f"Failed to enqueue: {idx}/{total} {path}")
            except Exception as e:
                self.logger.error(f"Error enqueuing {idx}/{total} {path}: {e}")

        # Incremental media size cache update (add sizes of enqueued files)
        if enqueued:
            added = 0
            for p in enqueued:
                try:
                    added += os.path.getsize(p)
                except Exception:
                    continue
            self._cached_media_size = max(0, getattr(self, '_cached_media_size', 0)) + added

        # Notify if any were added
        if enqueued and self._notifier:
            try:
                # During the initial pass (bot startup) we keep the grouped behaviour
                # and indicate initial scan to suppress metadata-heavy announcements.
                if is_first_pass:
                    self._notifier(enqueued, True)
                else:
                    # Group enqueued files by season tokens so a batch (season) does
                    # not spam one announcement per episode. We still allow single
                    # episode notifications to be sent individually.
                    throttle_ms = max(0, int(getattr(Config, 'WATCH_ANNOUNCE_THROTTLE_MS', 500)))
                    throttle_s = throttle_ms / 1000.0

                    def _extract_season_key(path: str) -> str:
                        """Return a grouping key for season batches.

                        We prefer patterns like S01E02, 1x02, "Season 1 Episode 02".
                        The key includes the containing directory plus the season number
                        so files from different folders but same season number are not
                        grouped together.
                        """
                        fname = os.path.basename(path)
                        parent = os.path.dirname(path)
                        # Common patterns: S01E02 or s01e02
                        m = re.search(r'[sS]?(\d{1,2})[xXeE](\d{2})', fname)
                        if m:
                            season = int(m.group(1))
                            return f"{parent}::season:{season}"
                        # Pattern like 'Season 1' or 'Season01' or 'Season_01'
                        m2 = re.search(r'[sS]eason[\s_\-]?(\d{1,2})', fname)
                        if m2:
                            season = int(m2.group(1))
                            return f"{parent}::season:{season}"
                        # Pattern like '1x02'
                        m3 = re.search(r'(?<!\d)(\d{1,2})[xX](\d{2})(?!\d)', fname)
                        if m3:
                            season = int(m3.group(1))
                            return f"{parent}::season:{season}"
                        # Fallback: group by parent folder only (one-off files will remain singletons)
                        return f"{parent}::season:0"

                    # Build groups
                    groups = {}
                    for p in enqueued:
                        key = _extract_season_key(p)
                        groups.setdefault(key, []).append(p)

                    # Notify per group: single-file groups are notified as singletons
                    for key, paths in groups.items():
                        try:
                            if len(paths) == 1:
                                self._notifier(paths, False)
                            else:
                                # For multi-episode season batches, notify once with the whole list
                                self._notifier(paths, False)
                        except Exception as e:
                            self.logger.error(f"Notifier error for group {key}: {e}")
                        # Sleep between group notifications to avoid hammering TMDB/Discord
                        try:
                            time.sleep(throttle_s)
                        except Exception:
                            pass
            except Exception as e:
                self.logger.error(f"Notifier error: {e}")

    def _init_media_size_cache_async(self):
        if getattr(self, "_size_thread", None) and self._size_thread.is_alive():
            return
        def worker():
            try:
                self.logger.info("Starting background media size calculation...")
                self._update_media_size_cache()
                self.logger.info(f"Media size cache initialized: {self._format_bytes(self._cached_media_size)}")
            except Exception as e:
                self.logger.error(f"Media size cache calculation failed: {e}")
        self._size_thread = threading.Thread(target=worker, name="MediaSizeCache", daemon=True)
        self._size_thread.start()

    @staticmethod
    def _format_bytes(n: int) -> str:
        units = ["B", "KB", "MB", "GB", "TB"]
        size = float(n)
        for u in units:
            if size < 1024 or u == units[-1]:
                return f"{size:.2f}{u}"
            size /= 1024

    # Initial scan completion helpers
    def has_initial_scan_completed(self) -> bool:
        return self._initial_scan_done.is_set()

    def wait_initial_scan_done(self, timeout: Optional[float] = None) -> bool:
        """Block the calling thread until the initial scan finishes or timeout expires.

        Returns True if completed, False if timeout occurred.
        """
        return self._initial_scan_done.wait(timeout)
