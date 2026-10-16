# Movie Request Integration (Overseerr/Jellyseerr) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let allowed Discord users request a movie via `/request movie <title>`, which the bot submits to a self-hosted Overseerr/Jellyseerr instance, tracks locally, and announces (with a mention) in a configurable channel once available. `/request status` lets a user check their own requests on demand.

**Architecture:** Two new service modules following existing patterns exactly — `OverseerrService` (a thin HTTP client shaped like `src/services/radarr_service.py`) and `MovieRequestTracker` (local JSON-backed state + a background polling thread shaped like `src/services/watch_folder_service.py`, wired into `bot.py` with the same `set_notifier()` + `run_coroutine_threadsafe` bridge the watch-folder announcer already uses). `tmdb_service.py` gains one new public method for multi-result search. `bot.py` gains a new `request_group` slash command group with two commands.

**Tech Stack:** Python, discord.py (`app_commands`, `discord.ui.View`/`Select`), `requests`, `tmdbsimple` (already a dependency).

**Spec:** `docs/superpowers/specs/2026-08-28-movie-requests-design.md`

## Global Constraints

- **No automated test suite in this repo** (see `CLAUDE.md`): every task's "test" step is a throwaway verification script run with `python3` (using `unittest.mock` where network calls need stubbing), not a permanent file under a `tests/` directory. Nothing under a test path gets committed — only the actual implementation/config/doc changes.
- Movies only — no TV show requests.
- Slash-only — no `!`-prefix equivalent for this feature.
- No inbound webhook/HTTP server — availability detection is polling only.
- Follow existing code style exactly: synchronous TMDB calls (no `run_in_executor` wrapping, matching every other `tmdb_service` call site in this repo), `requests` + `loop.run_in_executor` for all Overseerr HTTP calls (matching `RadarrService`), JSON persistence with load-on-init/save-on-write (matching `vlc_controller`'s queue backup).
- `Config.validate()` additions must warn (append to the errors list) rather than raise, matching the existing Radarr validation style.

---

### Task 1: Config plumbing for Overseerr/request settings

**Files:**
- Modify: `template.env` (after the Radarr block, before `# Queue Settings`)
- Modify: `src/config.py` (new class attributes, `validate()`, `print_config()`)

**Interfaces:**
- Produces: `Config.OVERSEERR_URL: str`, `Config.OVERSEERR_API_KEY: str`, `Config.REQUEST_CHANNEL_ID: int`, `Config.REQUEST_ANNOUNCE_CHANNEL_ID: int`, `Config.REQUEST_POLL_INTERVAL: int`, `Config.REQUEST_STORE_FILE: str` — all consumed by Tasks 2, 4, 5, 6.

- [ ] **Step 1: Add env vars to `template.env`**

Find this exact block near the end of the Radarr section:

```
# RADARR_ANIME_HOST=radarr-anime.local
# RADARR_ANIME_PORT=7878
# RADARR_ANIME_API_KEY=your_anime_api_key
# RADARR_ANIME_USE_SSL=false
# RADARR_ANIME_DISPLAY_NAME=Anime Movies

# Queue Settings
```

Insert a new block between the last `RADARR_ANIME_DISPLAY_NAME` line and `# Queue Settings`:

```bash
# RADARR_ANIME_DISPLAY_NAME=Anime Movies

# Movie Requests (Overseerr/Jellyseerr, Optional)
# Base URL of your self-hosted Overseerr/Jellyseerr instance (no trailing /api/v1).
OVERSEERR_URL=
OVERSEERR_API_KEY=
# Discord channel ID where /request movie and /request status are allowed.
# Set to 0 (default) to disable the whole feature.
REQUEST_CHANNEL_ID=0
# Where "now available" announcements are posted. Set to 0 to fall back to REQUEST_CHANNEL_ID.
REQUEST_ANNOUNCE_CHANNEL_ID=0
# Seconds between availability checks against Overseerr/Jellyseerr (minimum 60).
REQUEST_POLL_INTERVAL=900
# JSON file used to persist tracked requests, relative to the bot dir if not absolute.
REQUEST_STORE_FILE=movie_requests.json

# Queue Settings
```

- [ ] **Step 2: Add the new class attributes to `src/config.py`**

Find this exact block:

```python
    RADARR_INSTANCES: List[str] = [n.strip() for n in os.getenv('RADARR_INSTANCES', '').split(',') if n.strip()]
    
    # Queue Settings
```

Replace with:

```python
    RADARR_INSTANCES: List[str] = [n.strip() for n in os.getenv('RADARR_INSTANCES', '').split(',') if n.strip()]

    # Movie Requests (Overseerr/Jellyseerr, optional)
    OVERSEERR_URL: str = os.getenv('OVERSEERR_URL', '').strip()
    OVERSEERR_API_KEY: str = os.getenv('OVERSEERR_API_KEY', '').strip()
    REQUEST_CHANNEL_ID: int = int(os.getenv('REQUEST_CHANNEL_ID', '0'))
    REQUEST_ANNOUNCE_CHANNEL_ID: int = int(os.getenv('REQUEST_ANNOUNCE_CHANNEL_ID', '0'))
    REQUEST_POLL_INTERVAL: int = int(os.getenv('REQUEST_POLL_INTERVAL', '900'))
    REQUEST_STORE_FILE: str = os.getenv('REQUEST_STORE_FILE', 'movie_requests.json').strip()

    # Queue Settings
```

- [ ] **Step 3: Add validation rules**

Find this exact block (end of the Radarr validation in `validate()`):

```python
        else:
            # Single-instance mode: if any of the fields are set, require host+api
            if any([cls.RADARR_HOST, cls.RADARR_API_KEY]):
                if not (cls.RADARR_HOST and cls.RADARR_API_KEY):
                    errors.append("RADARR single-instance is partially configured; set both RADARR_HOST and RADARR_API_KEY or clear both")
            
        return errors
```

Replace with:

```python
        else:
            # Single-instance mode: if any of the fields are set, require host+api
            if any([cls.RADARR_HOST, cls.RADARR_API_KEY]):
                if not (cls.RADARR_HOST and cls.RADARR_API_KEY):
                    errors.append("RADARR single-instance is partially configured; set both RADARR_HOST and RADARR_API_KEY or clear both")

        # Movie request (Overseerr/Jellyseerr) validation (optional feature)
        if any([cls.OVERSEERR_URL, cls.OVERSEERR_API_KEY]):
            if not (cls.OVERSEERR_URL and cls.OVERSEERR_API_KEY):
                errors.append("Overseerr is partially configured; set both OVERSEERR_URL and OVERSEERR_API_KEY or clear both")
            elif cls.REQUEST_CHANNEL_ID <= 0:
                errors.append("REQUEST_CHANNEL_ID must be set to a valid Discord channel ID when Overseerr is configured")
        try:
            if cls.REQUEST_POLL_INTERVAL < 60:
                errors.append("REQUEST_POLL_INTERVAL must be at least 60 seconds")
        except ValueError:
            errors.append("REQUEST_POLL_INTERVAL must be a valid integer")

        return errors
```

- [ ] **Step 4: Add a summary line to `print_config()`**

Find this exact block:

```python
            (
                f"Radarr Instances: {', '.join(cls.RADARR_INSTANCES)}" if cls.RADARR_INSTANCES else (
                    f"Radarr (single): {'Configured' if (cls.RADARR_HOST and cls.RADARR_API_KEY) else 'Not Configured'}"
                )
            ),
            f"Discord Token: {'Configured' if cls.DISCORD_TOKEN else 'Not Configured'}",
```

Replace with:

```python
            (
                f"Radarr Instances: {', '.join(cls.RADARR_INSTANCES)}" if cls.RADARR_INSTANCES else (
                    f"Radarr (single): {'Configured' if (cls.RADARR_HOST and cls.RADARR_API_KEY) else 'Not Configured'}"
                )
            ),
            f"Overseerr: {'Configured' if (cls.OVERSEERR_URL and cls.OVERSEERR_API_KEY) else 'Not Configured'}",
            f"Request Channel: {cls.REQUEST_CHANNEL_ID if cls.REQUEST_CHANNEL_ID else 'Not Configured'}",
            f"Request Announce Channel: {cls.REQUEST_ANNOUNCE_CHANNEL_ID if cls.REQUEST_ANNOUNCE_CHANNEL_ID else 'Falls back to Request Channel'}",
            f"Discord Token: {'Configured' if cls.DISCORD_TOKEN else 'Not Configured'}",
```

- [ ] **Step 5: Verify manually**

Run:

```bash
python3 -c "
import os
os.environ['DISCORD_TOKEN'] = 'x'
os.environ['OVERSEERR_URL'] = 'http://localhost:5055'
os.environ['OVERSEERR_API_KEY'] = 'key'
os.environ['REQUEST_CHANNEL_ID'] = '0'
from src.config import Config
errors = Config.validate()
assert any('REQUEST_CHANNEL_ID' in e for e in errors), errors
print('OK: missing REQUEST_CHANNEL_ID caught')
"
```

Expected: prints `OK: missing REQUEST_CHANNEL_ID caught` with no traceback. Then run it again with `REQUEST_CHANNEL_ID=123` and confirm no such error appears, and with `REQUEST_POLL_INTERVAL=10` to confirm the poll-interval error appears.

- [ ] **Step 6: Commit**

```bash
git add template.env src/config.py
git commit -m "Add Overseerr/movie-request configuration"
```

---

### Task 2: `OverseerrService` — Overseerr/Jellyseerr API client

**Files:**
- Create: `src/services/overseerr_service.py`

**Interfaces:**
- Consumes: `Config.OVERSEERR_URL`, `Config.OVERSEERR_API_KEY` (from Task 1).
- Produces: `OverseerrService.is_configured() -> bool`; `async OverseerrService.request_movie(tmdb_id: int) -> dict` (`{"success": True, "request_id": int|None, "media_id": int|None}` or `{"success": False, "error": str}`); `async OverseerrService.get_movie_status(tmdb_id: int) -> dict` (`{"success": True, "status": int|None, "available": bool}` or `{"success": False, "error": str}`); module-level `STATUS_LABELS: dict[int, str]`. Consumed by Tasks 4, 5, 6.

- [ ] **Step 1: Write the implementation**

Create `src/services/overseerr_service.py`:

```python
import logging
import asyncio
import requests
from typing import Dict, Optional
from urllib.parse import urljoin


STATUS_LABELS = {
    1: "Unknown",
    2: "Pending",
    3: "Processing",
    4: "Partially Available",
    5: "Available",
}


class OverseerrService:
    def __init__(self, base_url: Optional[str] = None, api_key: Optional[str] = None):
        """Initialize Overseerr/Jellyseerr service using config or provided settings.

        Args:
            base_url: Base URL of the Seerr instance, e.g. http://localhost:5055 (defaults to config)
            api_key: Seerr API key (defaults to config)
        """
        try:
            from ..config import Config
        except Exception:
            Config = None  # type: ignore
        raw_base_url = base_url or (getattr(Config, 'OVERSEERR_URL', '') if Config else '')
        self.api_key = api_key or (getattr(Config, 'OVERSEERR_API_KEY', '') if Config else '')
        self.logger = logging.getLogger(__name__)

        if raw_base_url:
            self.base_url = raw_base_url.rstrip('/') + '/api/v1/'
        else:
            self.base_url = None

    def is_configured(self) -> bool:
        """Check if Overseerr/Jellyseerr is properly configured"""
        return bool(self.base_url and self.api_key)

    def _headers(self) -> Dict[str, str]:
        return {"X-Api-Key": self.api_key, "Content-Type": "application/json"}

    async def test_connection(self) -> Dict:
        """Test connection to the Overseerr/Jellyseerr server"""
        if not self.is_configured():
            return {"success": False, "error": "Overseerr not configured (missing URL or API key)"}
        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: requests.get(urljoin(self.base_url, "status"), headers=self._headers(), timeout=10)
            )
            if response.status_code == 200:
                data = response.json()
                return {"success": True, "message": f"Connected to Overseerr v{data.get('version', 'unknown')}"}
            return {"success": False, "error": f"HTTP {response.status_code}: {response.text}"}
        except requests.exceptions.RequestException as e:
            return {"success": False, "error": f"Connection error: {str(e)}"}
        except Exception as e:
            return {"success": False, "error": f"Unexpected error: {str(e)}"}

    async def request_movie(self, tmdb_id: int) -> Dict:
        """Submit a movie request to Overseerr/Jellyseerr by TMDB ID.

        Returns:
            Dict with 'success' boolean and 'request_id'/'media_id', or 'error' string
            (Seerr's own error message is passed through, e.g. already requested/available).
        """
        if not self.is_configured():
            return {"success": False, "error": "Overseerr not configured (missing URL or API key)"}
        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: requests.post(
                    urljoin(self.base_url, "request"),
                    headers=self._headers(),
                    json={"mediaType": "movie", "mediaId": tmdb_id},
                    timeout=15,
                )
            )
            if response.status_code in (200, 201):
                data = response.json()
                return {
                    "success": True,
                    "request_id": data.get("id"),
                    "media_id": (data.get("media") or {}).get("id"),
                }
            try:
                err = response.json().get("message") or response.text
            except Exception:
                err = response.text
            return {"success": False, "error": f"{err} (HTTP {response.status_code})"}
        except requests.exceptions.RequestException as e:
            return {"success": False, "error": f"Connection error: {str(e)}"}
        except Exception as e:
            return {"success": False, "error": f"Unexpected error: {str(e)}"}

    async def get_movie_status(self, tmdb_id: int) -> Dict:
        """Look up a movie's current media status on Overseerr/Jellyseerr by TMDB ID.

        Returns:
            Dict with 'success' boolean, 'status' (raw Seerr mediaInfo.status int, or None
            if the movie has never been requested/added), and 'available' (status == 5).
            'error' string on failure (network error, non-200, etc).
        """
        if not self.is_configured():
            return {"success": False, "error": "Overseerr not configured (missing URL or API key)"}
        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: requests.get(urljoin(self.base_url, f"movie/{tmdb_id}"), headers=self._headers(), timeout=10)
            )
            if response.status_code != 200:
                return {"success": False, "error": f"HTTP {response.status_code}: {response.text}"}
            data = response.json()
            status = (data.get("mediaInfo") or {}).get("status")
            return {"success": True, "status": status, "available": status == 5}
        except requests.exceptions.RequestException as e:
            return {"success": False, "error": f"Connection error: {str(e)}"}
        except Exception as e:
            return {"success": False, "error": f"Unexpected error: {str(e)}"}
```

- [ ] **Step 2: Verify manually (mocked HTTP, no live Seerr instance needed)**

Write this to a scratch file (e.g. `/tmp/verify_overseerr.py` or your scratchpad dir), run it with `python3 /tmp/verify_overseerr.py`, then delete it — it is not part of the codebase:

```python
import asyncio
import os
from unittest.mock import patch, MagicMock

os.environ.setdefault('DISCORD_TOKEN', 'x')
from src.services.overseerr_service import OverseerrService, STATUS_LABELS

svc = OverseerrService(base_url="http://localhost:5055/", api_key="testkey")
assert svc.base_url == "http://localhost:5055/api/v1/", svc.base_url
assert svc.is_configured()
assert STATUS_LABELS[5] == "Available"

with patch("requests.post") as mock_post:
    mock_post.return_value = MagicMock(status_code=201, json=lambda: {"id": 42, "media": {"id": 17}})
    result = asyncio.run(svc.request_movie(603))
    assert result == {"success": True, "request_id": 42, "media_id": 17}, result
    called_url = mock_post.call_args.args[0]
    assert called_url == "http://localhost:5055/api/v1/request", called_url
    assert mock_post.call_args.kwargs["json"] == {"mediaType": "movie", "mediaId": 603}
    assert mock_post.call_args.kwargs["headers"]["X-Api-Key"] == "testkey"

with patch("requests.post") as mock_post:
    mock_post.return_value = MagicMock(status_code=409, json=lambda: {"message": "Movie already requested"}, text="conflict")
    result = asyncio.run(svc.request_movie(603))
    assert result["success"] is False and "already requested" in result["error"], result

with patch("requests.get") as mock_get:
    mock_get.return_value = MagicMock(status_code=200, json=lambda: {"mediaInfo": {"status": 5}})
    result = asyncio.run(svc.get_movie_status(603))
    assert result == {"success": True, "status": 5, "available": True}, result

with patch("requests.get") as mock_get:
    mock_get.return_value = MagicMock(status_code=200, json=lambda: {"mediaInfo": {"status": 2}})
    result = asyncio.run(svc.get_movie_status(603))
    assert result == {"success": True, "status": 2, "available": False}, result

print("OK: OverseerrService verified")
```

Expected: `OK: OverseerrService verified` with no assertion errors or tracebacks.

- [ ] **Step 3: Commit**

```bash
git add src/services/overseerr_service.py
git commit -m "Add OverseerrService client for movie requests and status checks"
```

---

### Task 3: `tmdb_service.search_movies()` — multi-result search for pick lists

**Files:**
- Modify: `src/services/tmdb_service.py`

**Interfaces:**
- Produces: `TMDBService.search_movies(title: str, limit: int = 5) -> list[dict]`, each item
  `{"tmdb_id": int, "title": str, "year": int|None, "overview": str, "poster_path": str|None}`.
  Consumed by Task 5.

- [ ] **Step 1: Write the implementation**

In `src/services/tmdb_service.py`, add this new method to `TMDBService`, placed right after `_find_best_movie_result` (i.e. right before `_build_embed_from_movie_info`, so directly after the closing of the method that ends with `return best`):

```python
    def search_movies(self, title: str, limit: int = 5) -> list[dict]:
        """Search TMDB for movies matching a title, for user-facing pick lists.

        Unlike _find_best_movie_result (which collapses to a single best guess),
        this returns up to `limit` raw candidates in TMDB's own relevance order.

        Returns:
            List of dicts: {tmdb_id, title, year, overview, poster_path}. Empty list
            if unconfigured, no results, or on error.
        """
        if not self.api_key:
            self.logger.warning("No TMDB API key found")
            return []
        try:
            search = tmdb.Search()
            response = search.movie(query=title)
            results = (response or {}).get('results') or []
            candidates = []
            for item in results[:limit]:
                release_date = item.get('release_date') or ''
                year = int(release_date[:4]) if len(release_date) >= 4 and release_date[:4].isdigit() else None
                candidates.append({
                    'tmdb_id': item.get('id'),
                    'title': item.get('title') or item.get('original_title') or 'Untitled',
                    'year': year,
                    'overview': item.get('overview') or '',
                    'poster_path': item.get('poster_path'),
                })
            return candidates
        except Exception as e:
            self.logger.error(f"Error searching TMDB movies for title='{title}': {e}")
            return []
```

- [ ] **Step 2: Verify manually (mocked TMDB, no live API key needed)**

Write this to a scratch file, run with `python3`, then delete it:

```python
import os
from unittest.mock import patch, MagicMock

os.environ.setdefault('DISCORD_TOKEN', 'x')
os.environ['TMDB_API_KEY'] = 'testkey'
from src.services.tmdb_service import TMDBService

svc = TMDBService(api_key='testkey')

fake_response = {
    "results": [
        {"id": 603, "title": "The Matrix", "release_date": "1999-03-30", "overview": "A hacker...", "poster_path": "/p1.jpg"},
        {"id": 604, "title": "The Matrix Reloaded", "release_date": "2003-05-15", "overview": "Neo...", "poster_path": "/p2.jpg"},
    ]
}

with patch("tmdbsimple.Search") as MockSearch:
    instance = MockSearch.return_value
    instance.movie.return_value = fake_response
    results = svc.search_movies("The Matrix", limit=5)

assert len(results) == 2, results
assert results[0] == {"tmdb_id": 603, "title": "The Matrix", "year": 1999, "overview": "A hacker...", "poster_path": "/p1.jpg"}, results[0]
assert results[1]["year"] == 2003

with patch("tmdbsimple.Search") as MockSearch:
    instance = MockSearch.return_value
    instance.movie.return_value = {"results": []}
    assert svc.search_movies("Nonexistent Movie Title Xyz") == []

print("OK: search_movies verified")
```

Expected: `OK: search_movies verified` with no assertion errors or tracebacks.

- [ ] **Step 3: Commit**

```bash
git add src/services/tmdb_service.py
git commit -m "Add TMDBService.search_movies for multi-result pick lists"
```

---

### Task 4: `MovieRequestTracker` — local request tracking + availability poller

**Files:**
- Create: `src/services/movie_request_tracker.py`

**Interfaces:**
- Consumes: `Config.REQUEST_STORE_FILE`, `Config.REQUEST_POLL_INTERVAL` (Task 1); an `OverseerrService`-shaped object with `async get_movie_status(tmdb_id) -> dict` (Task 2).
- Produces: `MovieRequestTracker.__init__(overseerr_service, store_file=None, poll_interval=None)`; `add_request(record: dict) -> None`; `get_requests_for_user(user_id: int) -> list[dict]`; `set_notifier(callback: Callable[[dict], None]) -> None`; `start() -> bool`; `stop(timeout=5.0) -> None`; `poll_once() -> None` (synchronous, safe to call directly without starting the thread — this is what verification and the poll thread both call). Consumed by Tasks 5, 6.

- [ ] **Step 1: Write the implementation**

Create `src/services/movie_request_tracker.py`:

```python
import os
import json
import asyncio
import threading
import logging
from datetime import datetime, timezone
from typing import Callable, List, Optional

from ..config import Config


class MovieRequestTracker:
    def __init__(self, overseerr_service, store_file: Optional[str] = None, poll_interval: Optional[int] = None):
        self.overseerr = overseerr_service
        self.store_file = store_file if store_file is not None else Config.REQUEST_STORE_FILE
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
        """Check every un-notified record once against Overseerr. Synchronous
        (bridges into a short-lived event loop per check) so it can be called
        directly from a plain thread or from a verification script."""
        with self._lock:
            pending = [r for r in self._records if r.get("status") != "available"]

        changed = False
        for record in pending:
            tmdb_id = record.get("tmdb_id")
            try:
                result = asyncio.run(self.overseerr.get_movie_status(tmdb_id))
            except Exception as e:
                self.logger.error(f"Status check failed for tmdb_id={tmdb_id}: {e}")
                continue

            if not result.get("success"):
                self.logger.warning(f"Status check error for tmdb_id={tmdb_id}: {result.get('error')}")
                continue

            if result.get("available"):
                record["status"] = "available"
                record["notified_at"] = datetime.now(timezone.utc).isoformat()
                changed = True
                if self._notifier:
                    try:
                        self._notifier(record)
                    except Exception as e:
                        self.logger.error(f"Notifier callback failed for tmdb_id={tmdb_id}: {e}")

        if changed:
            with self._lock:
                self._save()
```

- [ ] **Step 2: Verify manually**

Write this to a scratch file, run with `python3`, then delete it:

```python
import os
import json
import tempfile

os.environ.setdefault('DISCORD_TOKEN', 'x')
from src.services.movie_request_tracker import MovieRequestTracker


class FakeOverseerr:
    def __init__(self):
        self.calls = []

    async def get_movie_status(self, tmdb_id):
        self.calls.append(tmdb_id)
        if tmdb_id == 603:
            return {"success": True, "status": 5, "available": True}
        if tmdb_id == 999:
            return {"success": False, "error": "boom"}
        return {"success": True, "status": 2, "available": False}


with tempfile.TemporaryDirectory() as tmp:
    store_path = os.path.join(tmp, "movie_requests.json")
    fake = FakeOverseerr()
    tracker = MovieRequestTracker(fake, store_file=store_path, poll_interval=999)

    notified = []
    tracker.set_notifier(lambda record: notified.append(record))

    tracker.add_request({"tmdb_id": 603, "title": "The Matrix", "year": 1999, "requested_by_id": 111, "requested_at": "2026-01-01T00:00:00+00:00", "status": "pending"})
    tracker.add_request({"tmdb_id": 604, "title": "The Matrix Reloaded", "year": 2003, "requested_by_id": 111, "requested_at": "2026-02-01T00:00:00+00:00", "status": "pending"})
    tracker.add_request({"tmdb_id": 999, "title": "Unreachable Movie", "year": 2020, "requested_by_id": 222, "requested_at": "2026-01-15T00:00:00+00:00", "status": "pending"})

    assert os.path.isfile(store_path)
    with open(store_path) as f:
        assert len(json.load(f)) == 3

    tracker.poll_once()

    assert len(notified) == 1 and notified[0]["tmdb_id"] == 603, notified
    with open(store_path) as f:
        saved = json.load(f)
    by_id = {r["tmdb_id"]: r for r in saved}
    assert by_id[603]["status"] == "available" and by_id[603]["notified_at"], by_id[603]
    assert by_id[604]["status"] == "pending"
    assert by_id[999]["status"] == "pending"  # error should not flip status

    user_111 = tracker.get_requests_for_user(111)
    assert len(user_111) == 2
    assert user_111[0]["tmdb_id"] == 604  # most recent requested_at first

    tracker2 = MovieRequestTracker(fake, store_file=store_path, poll_interval=999)
    assert len(tracker2.get_requests_for_user(111)) == 2  # reload from disk works

print("OK: MovieRequestTracker verified")
```

Expected: `OK: MovieRequestTracker verified` with no assertion errors or tracebacks.

- [ ] **Step 3: Commit**

```bash
git add src/services/movie_request_tracker.py
git commit -m "Add MovieRequestTracker for local request state and availability polling"
```

---

### Task 5: `/request movie` — search, pick, and submit

**Files:**
- Modify: `bot.py`

**Interfaces:**
- Consumes: `TMDBService.search_movies` (Task 3), `OverseerrService` (Task 2), `MovieRequestTracker` (Task 4), `Config.REQUEST_CHANNEL_ID` (Task 1), existing `_check_allowed_roles_for_interaction` helper.
- Produces: `request_group` (`app_commands.Group`) registered in `_register_app_command_groups()`; module-level `overseerr_service: OverseerrService` and `movie_request_tracker: MovieRequestTracker` instances, importable by Task 6's code in the same file.

- [ ] **Step 1: Add the `timezone` import**

Find:

```python
from datetime import datetime
```

Replace with:

```python
from datetime import datetime, timezone
```

- [ ] **Step 2: Instantiate the new services**

Find this exact block:

```python
from src.services.vlc_controller import VLCController
from src.services.tmdb_service import TMDBService
from src.services.watch_folder_service import WatchFolderService
from src.utils.media_utils import MediaUtils
from src.services.radarr_service import RadarrService

vlc = VLCController(bot=bot)
tmdb_service = TMDBService()
watch_service = WatchFolderService(vlc)
_radarr_services = []
```

Replace with:

```python
from src.services.vlc_controller import VLCController
from src.services.tmdb_service import TMDBService
from src.services.watch_folder_service import WatchFolderService
from src.utils.media_utils import MediaUtils
from src.services.radarr_service import RadarrService
from src.services.overseerr_service import OverseerrService, STATUS_LABELS
from src.services.movie_request_tracker import MovieRequestTracker

vlc = VLCController(bot=bot)
tmdb_service = TMDBService()
watch_service = WatchFolderService(vlc)
overseerr_service = OverseerrService()
movie_request_tracker = MovieRequestTracker(overseerr_service)
_radarr_services = []
```

- [ ] **Step 3: Declare the `request_group` and the movie-search select view**

Find this exact block:

```python
watch_group = app_commands.Group(name="watch", description="CtrlVee watch folder management")
admin_group = app_commands.Group(name="admin", description="CtrlVee owner administration")
```

Replace with:

```python
watch_group = app_commands.Group(name="watch", description="CtrlVee watch folder management")
admin_group = app_commands.Group(name="admin", description="CtrlVee owner administration")
request_group = app_commands.Group(name="request", description="CtrlVee media requests")
```

- [ ] **Step 4: Add the channel-restriction helper, the select view, and the `/request movie` handler**

Find this exact block (end of `_check_allowed_roles_for_interaction`):

```python
    await interaction.response.send_message(
        f"You need one of these roles to use this command: {_format_allowed_roles_for_display()}",
        ephemeral=True,
    )
    return False


def _build_system_help_embed() -> discord.Embed:
```

Replace with:

```python
    await interaction.response.send_message(
        f"You need one of these roles to use this command: {_format_allowed_roles_for_display()}",
        ephemeral=True,
    )
    return False


async def _check_request_channel_for_interaction(interaction: discord.Interaction) -> bool:
    """Return True when movie requests are configured and used in the right channel."""
    if not Config.REQUEST_CHANNEL_ID or not overseerr_service.is_configured():
        await interaction.response.send_message(
            "Movie requests are not configured on this bot.", ephemeral=True
        )
        return False
    if interaction.channel_id != Config.REQUEST_CHANNEL_ID:
        await interaction.response.send_message(
            f"Please use this command in <#{Config.REQUEST_CHANNEL_ID}>.", ephemeral=True
        )
        return False
    return True


def _build_movie_request_embed(candidate: dict, *, title_prefix: str = "", description: Optional[str] = None) -> discord.Embed:
    year_text = f" ({candidate['year']})" if candidate.get('year') else ""
    embed = discord.Embed(
        title=f"{title_prefix}{candidate['title']}{year_text}",
        description=description if description is not None else (candidate.get('overview') or "No overview available."),
        color=discord.Color.purple(),
    )
    if candidate.get('poster_path'):
        embed.set_thumbnail(url=f"https://image.tmdb.org/t/p/w500{candidate['poster_path']}")
    return embed


class MovieRequestSelect(discord.ui.Select):
    def __init__(self, candidates: list[dict], requester: discord.abc.User):
        self.candidates_by_value = {str(c['tmdb_id']): c for c in candidates}
        self.requester = requester
        options = [
            discord.SelectOption(
                label=f"{c['title']} ({c['year']})" if c.get('year') else c['title'],
                value=str(c['tmdb_id']),
                description=(c.get('overview') or "")[:100] or None,
            )
            for c in candidates
        ]
        super().__init__(placeholder="Select the correct movie...", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.requester.id:
            await interaction.response.send_message("Only the person who ran this command can pick a result.", ephemeral=True)
            return

        candidate = self.candidates_by_value[self.values[0]]
        submitting_embed = _build_movie_request_embed(candidate, title_prefix="Submitting request: ")
        await interaction.response.edit_message(embed=submitting_embed, view=None)

        result = await overseerr_service.request_movie(candidate['tmdb_id'])
        if result.get("success"):
            movie_request_tracker.add_request({
                "tmdb_id": candidate['tmdb_id'],
                "title": candidate['title'],
                "year": candidate.get('year'),
                "overview": candidate.get('overview') or "",
                "poster_path": candidate.get('poster_path'),
                "overseerr_request_id": result.get("request_id"),
                "overseerr_media_id": result.get("media_id"),
                "requested_by_id": self.requester.id,
                "requested_by_name": str(self.requester),
                "requested_at": datetime.now(timezone.utc).isoformat(),
                "status": "pending",
                "notified_at": None,
            })
            confirmed_embed = _build_movie_request_embed(
                candidate, title_prefix="✅ Requested: ",
                description=f"Requested by {self.requester.mention}. You'll be notified here once it's available.\n\n{candidate.get('overview') or ''}",
            )
            await interaction.edit_original_response(embed=confirmed_embed)
        else:
            failed_embed = _build_movie_request_embed(
                candidate, title_prefix="❌ Request failed: ",
                description=result.get("error", "Unknown error"),
            )
            await interaction.edit_original_response(embed=failed_embed)


class MovieRequestSelectView(discord.ui.View):
    def __init__(self, candidates: list[dict], requester: discord.abc.User):
        super().__init__(timeout=60)
        self.message: Optional[discord.Message] = None
        self.add_item(MovieRequestSelect(candidates, requester))

    async def on_timeout(self) -> None:
        if self.message is None:
            return
        for item in self.children:
            item.disabled = True
        try:
            await self.message.edit(content="Selection timed out.", view=self)
        except Exception:
            pass


def _build_system_help_embed() -> discord.Embed:
```

- [ ] **Step 5: Add the `/request movie` command handler**

Find this exact block (last handler in the file, right before the module-level `_register_app_command_groups()` call):

```python
@admin_group.command(name="cleanup-playlist", description="Owner only: remove missing files from VLC playlist")
async def admin_cleanup_playlist(interaction: discord.Interaction):
    await _run_playlist_cleanup(interaction, "/admin cleanup-playlist")


_register_app_command_groups()
```

Replace with:

```python
@admin_group.command(name="cleanup-playlist", description="Owner only: remove missing files from VLC playlist")
async def admin_cleanup_playlist(interaction: discord.Interaction):
    await _run_playlist_cleanup(interaction, "/admin cleanup-playlist")


@request_group.command(name="movie", description="Request a movie via Overseerr/Jellyseerr")
@app_commands.describe(title="Movie title to search for")
async def request_movie(interaction: discord.Interaction, title: str):
    if not await _check_request_channel_for_interaction(interaction):
        return
    if not await _check_allowed_roles_for_interaction(interaction):
        return

    await interaction.response.defer(thinking=True)

    candidates = tmdb_service.search_movies(title)
    if not candidates:
        await interaction.followup.send(f"No results found for '{title}'.")
        return

    view = MovieRequestSelectView(candidates, interaction.user)
    embed = discord.Embed(
        title="Which movie did you mean?",
        description="\n".join(
            f"**{c['title']}**" + (f" ({c['year']})" if c.get('year') else "") for c in candidates
        ),
        color=discord.Color.blue(),
    )
    message = await interaction.followup.send(embed=embed, view=view)
    view.message = message


_register_app_command_groups()
```

- [ ] **Step 6: Register `request_group` in `_register_app_command_groups()`**

Find:

```python
    groups = [
        system_group,
        playback_group,
        playlist_group,
        queue_group,
        subtitles_group,
        audio_group,
        schedule_group,
        watch_group,
        admin_group,
    ]
```

Replace with:

```python
    groups = [
        system_group,
        playback_group,
        playlist_group,
        queue_group,
        subtitles_group,
        audio_group,
        schedule_group,
        watch_group,
        admin_group,
        request_group,
    ]
```

- [ ] **Step 7: Import `Optional`**

`bot.py` uses `Optional[...]` type hints in the new code above, and has no existing `typing` import. Find this exact block at the top of the file:

```python
import sys
import os
import asyncio
import logging
import threading
import re
import time
from datetime import datetime, timezone
```

Replace with:

```python
import sys
import os
import asyncio
import logging
import threading
import re
import time
from datetime import datetime, timezone
from typing import Optional
```

- [ ] **Step 8: Verify syntax and wiring**

```bash
python3 -m py_compile bot.py
```

Expected: no output, exit code 0.

Then, with a real Discord token and (if you have one handy) a real Overseerr/Jellyseerr instance configured in `.env` (`OVERSEERR_URL`, `OVERSEERR_API_KEY`, `REQUEST_CHANNEL_ID`, plus `ALLOWED_ROLES` already set), run the bot and manually test in Discord:

1. `/request movie <title>` in the configured channel, as a user with an allowed role → verify the pick-list embed appears, selecting an option submits the request, and the message updates to a confirmation embed.
2. Check `movie_requests.json` in the bot's working directory now contains the new record.
3. Try the command in a different channel, and as a user without an allowed role → verify both are rejected with the expected messages.
4. Run it again for the same movie → verify Overseerr/Jellyseerr's duplicate-request error is shown in the failure embed.

If you don't have a live Overseerr/Jellyseerr instance available right now, it's fine to defer steps 1-4 to Task 6's end-to-end check (which needs one anyway for the availability/notification path) — just confirm `py_compile` passes here.

- [ ] **Step 9: Commit**

```bash
git add bot.py
git commit -m "Add /request movie command with TMDB search-and-pick UX"
```

---

### Task 6: `/request status`, availability notifier, startup wiring, and docs

**Files:**
- Modify: `bot.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: `MovieRequestTracker.get_requests_for_user`, `.set_notifier`, `.start` (Task 4); `OverseerrService.get_movie_status`, `STATUS_LABELS` (Task 2); `Config.REQUEST_ANNOUNCE_CHANNEL_ID` (Task 1).

- [ ] **Step 1: Add the `/request status` command handler**

Find this exact block (the `/request movie` handler added in Task 5):

```python
    message = await interaction.followup.send(embed=embed, view=view)
    view.message = message


_register_app_command_groups()
```

Replace with:

```python
    message = await interaction.followup.send(embed=embed, view=view)
    view.message = message


@request_group.command(name="status", description="Check the status of your movie requests")
async def request_status(interaction: discord.Interaction):
    if not await _check_request_channel_for_interaction(interaction):
        return
    if not await _check_allowed_roles_for_interaction(interaction):
        return

    await interaction.response.defer(thinking=True, ephemeral=True)

    records = movie_request_tracker.get_requests_for_user(interaction.user.id)
    if not records:
        await interaction.followup.send("You haven't requested anything yet.")
        return

    lines = []
    for record in records:
        if record.get("status") == "available":
            label = STATUS_LABELS[5]
        else:
            result = await overseerr_service.get_movie_status(record["tmdb_id"])
            if result.get("success"):
                label = STATUS_LABELS.get(result.get("status"), "Unknown")
            else:
                # Live check failed (e.g. Seerr temporarily unreachable) — fall back
                # to the last known local status rather than showing an error.
                label = record.get("status", "pending").title()
        year_text = f" ({record['year']})" if record.get('year') else ""
        lines.append(f"**{record['title']}**{year_text} — {label}")

    embed = discord.Embed(
        title="Your Movie Requests",
        description="\n".join(lines),
        color=discord.Color.blue(),
    )
    await interaction.followup.send(embed=embed)


_register_app_command_groups()
```

- [ ] **Step 2: Wire the availability notifier and start the tracker**

Find this exact block:

```python
            watch_service.set_notifier(notifier)

        started = watch_service.start()
        if started:
            logger.info("WatchFolderService started")
        else:
            logger.info("WatchFolderService not started (disabled or already running)")
```

Replace with:

```python
            watch_service.set_notifier(notifier)

        started = watch_service.start()
        if started:
            logger.info("WatchFolderService started")
        else:
            logger.info("WatchFolderService not started (disabled or already running)")

        def movie_request_notifier(record: dict) -> None:
            async def _send_availability_announcement():
                channel_id = Config.REQUEST_ANNOUNCE_CHANNEL_ID or Config.REQUEST_CHANNEL_ID
                if not channel_id:
                    return
                channel = bot.get_channel(channel_id)
                if channel is None:
                    try:
                        channel = await bot.fetch_channel(channel_id)
                    except Exception as e:
                        logger.error(f"Failed to fetch movie request announce channel {channel_id}: {e}")
                        return
                embed = discord.Embed(
                    title="🎬 Now Available",
                    description=f"**{record.get('title')}**" + (f" ({record.get('year')})" if record.get('year') else "") + " is now available to watch.",
                    color=discord.Color.green(),
                )
                if record.get('overview'):
                    embed.add_field(name="Overview", value=record['overview'][:1024], inline=False)
                if record.get('poster_path'):
                    embed.set_thumbnail(url=f"https://image.tmdb.org/t/p/w500{record['poster_path']}")
                try:
                    await channel.send(
                        content=f"<@{record.get('requested_by_id')}> your requested movie is now available!",
                        embed=embed,
                    )
                except discord.Forbidden:
                    logger.warning(f"Missing permission to send movie request announcement in channel {channel_id}.")
                except Exception as e:
                    logger.error(f"Failed to send movie request announcement: {e}")

            try:
                future = asyncio.run_coroutine_threadsafe(_send_availability_announcement(), bot.loop)

                def _log_result(fut):
                    try:
                        fut.result()
                    except Exception as e:
                        logger.error(f"Movie request announcement task failed: {e}", exc_info=True)

                future.add_done_callback(_log_result)
            except Exception as e:
                logger.error(f"Failed to schedule movie request announcement: {e}", exc_info=True)

        movie_request_tracker.set_notifier(movie_request_notifier)
        request_tracker_started = movie_request_tracker.start()
        if request_tracker_started:
            logger.info("MovieRequestTracker started")
        else:
            logger.info("MovieRequestTracker not started (no REQUEST_STORE_FILE configured)")
```

- [ ] **Step 3: Verify syntax**

```bash
python3 -m py_compile bot.py
```

Expected: no output, exit code 0.

- [ ] **Step 4: Update `README.md` — configuration section**

Find this exact block:

```
RADARR_ANIME_USE_SSL=true
RADARR_ANIME_DISPLAY_NAME=Anime
```

## Commands
```

Replace with:

```
RADARR_ANIME_USE_SSL=true
RADARR_ANIME_DISPLAY_NAME=Anime
```

### Movie Requests (Overseerr/Jellyseerr, Optional)

Lets allowed users request movies via `/request movie <title>` (slash-only), tracked
locally and announced when available. Availability is checked by polling — no
inbound webhook is needed.

```bash
OVERSEERR_URL=http://localhost:5055
OVERSEERR_API_KEY=your_overseerr_or_jellyseerr_api_key
REQUEST_CHANNEL_ID=123456789012345678
REQUEST_ANNOUNCE_CHANNEL_ID=0
REQUEST_POLL_INTERVAL=900
REQUEST_STORE_FILE=movie_requests.json
```

## Commands
```

- [ ] **Step 5: Update `README.md` — commands section**

Find this exact block:

```
### Info / Utility

- `!status`
- `!version`
- `!privacy` (aliases: `policy`, `data_policy`)
- `!changelog` (aliases: `changes`, `whatsnew`)
- `!controls`

## Privacy Statement
```

Replace with:

```
### Info / Utility

- `!status`
- `!version`
- `!privacy` (aliases: `policy`, `data_policy`)
- `!changelog` (aliases: `changes`, `whatsnew`)
- `!controls`

### Movie Requests (Slash Only)

- `/request movie <title>` — search and request a movie via Overseerr/Jellyseerr
- `/request status` — check the status of your own requests

## Privacy Statement
```

- [ ] **Step 6: End-to-end manual verification**

With `.env` fully configured (`OVERSEERR_URL`, `OVERSEERR_API_KEY`, `REQUEST_CHANNEL_ID`, `ALLOWED_ROLES`, `TMDB_API_KEY`) and a real Overseerr/Jellyseerr instance running:

1. Run the bot; confirm `MovieRequestTracker started` appears in the logs.
2. `/request movie <title>` → pick a result → confirm the confirmation embed and a new entry in `movie_requests.json`.
3. `/request status` → confirm it shows the movie with a `Pending`/`Processing` label.
4. In Overseerr/Jellyseerr, mark that movie as available (approve + mark available, or wait for a real download).
5. Within one `REQUEST_POLL_INTERVAL`, confirm the bot posts to the announce channel with the `@mention` and correct metadata.
6. `/request status` again → confirm it now shows `Available` without waiting on a fresh Overseerr call (served from the local record).
7. Temporarily set `OVERSEERR_URL` to an unreachable host and restart → confirm `/request movie` fails gracefully and the bot doesn't crash.

- [ ] **Step 7: Commit**

```bash
git add bot.py README.md
git commit -m "Add /request status, availability notifier, and docs for movie requests"
```
