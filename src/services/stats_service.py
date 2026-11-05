import json
import logging
import os

logger = logging.getLogger(__name__)

FINISHED_FRACTION = 0.85  # Share of the runtime a viewer must see; leaves room for credits
BUCKET_SECONDS = 60


class StatsService:
    """Per-user watch time and finished titles, kept in a local JSON file."""

    def __init__(self, path: str = "watch_stats.json", excluded=()):
        self.path = path
        self.excluded = {str(user_id) for user_id in excluded}
        self.users = self._load()

    def _load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return {}
        except ValueError as e:
            # Keep the bad file: the next save would otherwise wipe the ladder for good
            logger.error(f"Unreadable {self.path}, moving it to {self.path}.corrupt: {e}")
            os.replace(self.path, f"{self.path}.corrupt")
            return {}

    def save(self) -> None:
        tmp_path = f"{self.path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(self.users, f)
        os.replace(tmp_path, self.path)

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
