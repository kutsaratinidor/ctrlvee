import json
import logging
import os
import time

logger = logging.getLogger(__name__)

FINISHED_FRACTION = 0.85  # Share of the runtime a viewer must see; leaves room for credits
BUCKET_SECONDS = 60
RECENT_MIN_SECONDS = 300  # Room time before a title counts as viewed, so a channel-hop doesn't list it
RECENT_KEEP = 20
POSTER_RETRY_SECONDS = 3600  # TMDB returns nothing on errors as well as on no match, so try again later


class StatsService:
    """Per-user watch time and finished titles, kept in a local JSON file."""

    def __init__(self, path: str = "watch_stats.json", excluded=()):
        self.path = path
        self.excluded = {str(user_id) for user_id in excluded}
        # Recently viewed titles live in their own file so the ladder file keeps its format
        self.recent_path = f"{os.path.splitext(path)[0]}_recent.json"
        self.users = self._load(self.path, {})
        self.recent = self._load(self.recent_path, [])
        for entry in self.recent:
            # 1.22 kept {user_id: name} and one room total; the total becomes each viewer's time
            total = entry.pop("seconds", 0)
            for user_id, viewer in entry["viewers"].items():
                if isinstance(viewer, str):
                    entry["viewers"][user_id] = {"name": viewer, "seconds": total}

    @staticmethod
    def _load(path: str, empty):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return empty
        except ValueError as e:
            # Keep the bad file: the next save would otherwise wipe it for good
            logger.error(f"Unreadable {path}, moving it to {path}.corrupt: {e}")
            os.replace(path, f"{path}.corrupt")
            return empty

    def save(self) -> None:
        for path, data in ((self.path, self.users), (self.recent_path, self.recent)):
            tmp_path = f"{path}.tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp_path, path)

    def record_view(self, key: str | None, name: str, episode: str | None, viewers: dict, seconds: int) -> dict | None:
        """Note that non-excluded `viewers` ({user_id: name}) watched `key` for `seconds`.

        Playback with only excluded accounts (the streamer) in the room is ignored, so an
        empty room never shows up as viewed. Time is kept per viewer, so excluding someone
        later removes their share too. Returns the entry, newest first in `self.recent`.
        """
        watchers = {str(u): n for u, n in viewers.items() if str(u) not in self.excluded}
        if not key or not watchers:
            return None
        entry = next((e for e in self.recent if e["key"] == key), None)
        if entry is None:
            entry = {"key": key, "name": name, "viewers": {}}
        else:
            self.recent.remove(entry)
        self.recent.insert(0, entry)
        entry.update(name=name, episode=episode, last=int(time.time()))
        for user_id, viewer_name in watchers.items():
            viewer = entry["viewers"].setdefault(user_id, {"name": viewer_name, "seconds": 0})
            viewer["name"] = viewer_name
            viewer["seconds"] += seconds
        while len(self.recent) > RECENT_KEEP:
            # Drop the oldest title nobody really watched before any that was, so channel-hops
            # can't push viewed titles off the grid. The newest may still be counting up to it.
            self.recent.remove(next(
                (e for e in reversed(self.recent[1:]) if not self.is_viewed(e)), self.recent[-1]
            ))
        return entry

    def is_viewed(self, entry: dict) -> bool:
        """Someone not excluded has been in the room for RECENT_MIN_SECONDS of it."""
        return any(
            v["seconds"] >= RECENT_MIN_SECONDS
            for user_id, v in entry["viewers"].items() if user_id not in self.excluded
        )

    def poster_due(self, limit: int, now: float) -> dict | None:
        """First of the `limit` shown entries still without a poster: never looked up, or
        looked up with no result over POSTER_RETRY_SECONDS ago."""
        shown = [e for e in self.recent if self.is_viewed(e)][:limit]
        return next((
            e for e in shown
            if "poster" not in e
            or (e["poster"] is None and now - e.get("poster_checked", 0) >= POSTER_RETRY_SECONDS)
        ), None)

    def recently_viewed(self, limit: int = 5) -> list:
        """Newest viewed entries, as copies holding only viewers who aren't excluded now,
        so adding someone to STATS_EXCLUDED_USER_IDS hides them from past cards too."""
        return [
            {**e, "viewers": {u: v for u, v in e["viewers"].items() if u not in self.excluded}}
            for e in self.recent if self.is_viewed(e)
        ][:limit]

    def record(self, viewers: dict, title_key: str | None, position: int, length: int, seconds: int) -> None:
        """Credit each viewer ({user_id: name}) for `seconds` at `position` of a `length`-second title.

        Progress is the set of minutes seen rather than a running total, so rewinds
        don't count twice and skipped parts don't count at all.
        """
        bucket = position // BUCKET_SECONDS
        needed = -(-length // BUCKET_SECONDS) * FINISHED_FRACTION
        for user_id, name in viewers.items():
            user = self.users.setdefault(str(user_id), {"name": name, "seconds": 0, "finished": [], "progress": {}})
            user["name"] = name
            user["seconds"] += seconds
            if not title_key or length <= 0 or title_key in user["finished"]:
                continue
            seen = user["progress"].setdefault(title_key, [])
            if bucket not in seen:
                seen.append(bucket)
            if len(seen) >= needed:
                user["finished"].append(title_key)
                del user["progress"][title_key]

    def ladders(self, limit: int = 10) -> tuple[list, list]:
        """Top (name, seconds) by watch time and top (name, count) by finished titles."""
        users = [u for user_id, u in self.users.items() if user_id not in self.excluded]
        hours = sorted(((u["name"], u["seconds"]) for u in users if u["seconds"]), key=lambda r: -r[1])
        finished = sorted(((u["name"], len(u["finished"])) for u in users if u["finished"]), key=lambda r: -r[1])
        return hours[:limit], finished[:limit]
