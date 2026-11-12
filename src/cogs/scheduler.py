

import discord
from discord.ext import commands, tasks
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import logging
import json
import os
import re
from urllib.parse import unquote
from ..config import Config
from ..utils.media_utils import MediaUtils

# Cross-platform timezone handling for PH time
def get_ph_timezone():
    try:
        return ZoneInfo("Asia/Manila")
    except ZoneInfoNotFoundError:
        try:
            # On Windows, try Singapore Standard Time (same UTC+8)
            return ZoneInfo("Singapore Standard Time")
        except Exception:
            from tzlocal import get_localzone
            # Fallback: use system local timezone (may be incorrect)
            return get_localzone()

PH_TZ = get_ph_timezone()


def format_schedule_time(dt) -> str:
    """PH time plus Discord timestamps, which each viewer's client renders in their own timezone."""
    if not isinstance(dt, datetime):
        return str(dt)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=PH_TZ)
    ts = int(dt.timestamp())
    return f"{dt.strftime('%Y-%m-%d %H:%M %Z')} · <t:{ts}:f> (<t:{ts}:R>)"
logger = logging.getLogger(__name__)


def _title_key(name: str):
    title, year = MediaUtils.parse_movie_filename(unquote(name or ""))
    norm = re.sub(r"\W+", " ", (title or "").lower()).strip()
    return (norm, year) if norm else None


def _resolve_schedule_index(entry: dict, items: list, exists=lambda uri: True):
    """0-based playlist index for a schedule, or None.

    Prefers the exact file (uri), so the schedule survives index shifts from
    playlist cleanup. If that file is gone from the playlist or from disk (e.g.
    replaced by an upgrade), falls back to the newest existing item with the same
    parsed title + year. A missing-on-disk exact match is still returned when no
    replacement exists, so an unreachable share doesn't silently skip the schedule.
    """
    uri = entry.get("uri")
    if not uri:  # legacy entries only stored the number
        idx = entry["number"] - 1
        return idx if 0 <= idx < len(items) else None
    exact = next((i for i, leaf in enumerate(items) if leaf.get("uri") == uri), None)
    if exact is not None and exists(uri):
        return exact
    want = _title_key(uri.rsplit("/", 1)[-1])
    matches = [
        i for i, leaf in enumerate(items)
        if want and i != exact and _title_key(leaf.get("name", "")) == want and exists(leaf.get("uri"))
    ]
    return matches[-1] if matches else exact


SCHEDULE_BACKUP_FILE = "schedule_backup.json"


from src.services.tmdb_service import TMDBService
from ..utils.command_utils import format_cmd_inline

class Scheduler(commands.Cog):
    def __init__(self, bot, vlc_controller):
        self.bot = bot
        self.vlc = vlc_controller
        self.tmdb = TMDBService()
        self.scheduled = self._load_schedule_backup()
        self.pre_announce_sent = set()  # (number, dt) tuples
        self.check_schedules.start()
    def __init__(self, bot, vlc_controller):
        self.bot = bot
        self.vlc = vlc_controller
        self.tmdb = TMDBService()
        self.scheduled = self._load_schedule_backup()
        self.check_schedules.start()

    def cog_unload(self):
        self.check_schedules.cancel()
        self._save_schedule_backup()

    def _save_schedule_backup(self):
        try:
            with open(SCHEDULE_BACKUP_FILE, "w") as f:
                json.dump(self.scheduled, f, default=str)
        except Exception as e:
            logger.error(f"Error saving schedule backup: {e}")

    def _load_schedule_backup(self):
        if os.path.exists(SCHEDULE_BACKUP_FILE):
            try:
                with open(SCHEDULE_BACKUP_FILE, "r") as f:
                    data = json.load(f)
                # Convert dt strings back to datetime
                for s in data:
                    if isinstance(s.get("dt"), str):
                        s["dt"] = datetime.fromisoformat(s["dt"])
                return data
            except Exception as e:
                logger.error(f"Error loading schedule backup: {e}")
        return []

    def _uri_exists(self, uri) -> bool:
        path = self.vlc._uri_to_path(uri)
        return bool(path) and self.vlc._file_exists_robust(path)

    def _rebind_due_schedules(self, now) -> dict:
        """Resolve schedules due within 10 minutes; update number/uri if the item moved."""
        due = [s for s in self.scheduled if (s["dt"] - now).total_seconds() <= 600]
        if not due:
            return {}
        playlist = self.vlc.get_playlist()
        items = playlist.findall('.//leaf') if playlist is not None else []
        resolved, changed = {}, False
        for s in due:
            idx = _resolve_schedule_index(s, items, self._uri_exists)
            resolved[id(s)] = (idx, items[idx] if idx is not None else None)
            if idx is None:
                continue
            leaf = items[idx]
            if s["number"] != idx + 1 or (s.get("uri") and s["uri"] != leaf.get("uri")):
                logger.info(f"Rebinding schedule '{s.get('title')}' #{s['number']} -> #{idx + 1} ({leaf.get('name')})")
                s["number"] = idx + 1
                s["uri"] = leaf.get("uri") or s.get("uri")
                changed = True
        if changed:
            self._save_schedule_backup()
        return resolved

    @commands.command(name="schedule")
    @commands.has_any_role(*Config.ALLOWED_ROLES)
    async def schedule_movie(self, ctx, number: int, date: str, time: str):
        """Schedule a movie by playlist number and PH time.

        Usage: shown with the configured command prefix.
        """
        try:
            dt = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")
            dt = dt.replace(tzinfo=PH_TZ)
            now = datetime.now(PH_TZ)
            if dt <= now:
                await ctx.send("❌ Scheduled time must be in the future (PH time).")
                return
            # Check for double scheduling (same movie, same time within 1 minute)
            for s in self.scheduled:
                if s["number"] == number and abs((s["dt"] - dt).total_seconds()) < 60:
                    await ctx.send(f"❌ Movie #{number} is already scheduled at {format_schedule_time(s['dt'])}. Double scheduling is not allowed.")
                    return
            # Get movie title and duration from playlist (on demand)
            playlist = self.vlc.get_playlist()
            items = playlist.findall('.//leaf') if playlist is not None else []
            idx = number - 1
            if not (0 <= idx < len(items)):
                await ctx.send(f"❌ Movie number {number} is out of bounds. There are {len(items)} items in the playlist.")
                return
            filename = items[idx].get('name', 'Unknown')
            title = MediaUtils.clean_movie_title(filename)
            item_uri = items[idx].get('uri')
            duration = MediaUtils.get_media_duration(items[idx])
            entry = {
                "number": number,
                "title": title,
                "uri": item_uri,
                "dt": dt,
                "user": ctx.author.id,
                "channel": ctx.channel.id,
                "duration": duration
            }
            self.scheduled.append(entry)
            self._save_schedule_backup()
            if duration == 'Loading...':
                dur_str = 'Loading...'
            elif duration:
                dur_str = MediaUtils.format_time(duration)
            else:
                dur_str = "Unknown"
            embed = discord.Embed(
                title="Movie Scheduled",
                color=discord.Color.green()
            )
            embed.add_field(name="Number", value=f"#{number}", inline=True)
            embed.add_field(name="Title", value=title, inline=True)
            embed.add_field(name="Scheduled For", value=format_schedule_time(dt), inline=False)
            embed.add_field(name="Duration", value=dur_str, inline=True)
            embed.add_field(name="Scheduled By", value=ctx.author.mention, inline=True)
            await ctx.send(embed=embed)
        except Exception as e:
            await ctx.send(f"❌ Invalid date/time format. Use: {format_cmd_inline('schedule <number> <YYYY-MM-DD> <HH:MM>')}")

    @commands.command(name="schedules")
    @commands.has_any_role(*Config.ALLOWED_ROLES)
    async def list_schedules(self, ctx):
        """List all upcoming scheduled movies."""
        if not self.scheduled:
            await ctx.send("No movies scheduled.")
            return
        embed = discord.Embed(title="Upcoming Scheduled Movies", color=discord.Color.purple())
        for s in sorted(self.scheduled, key=lambda x: x["dt"]):
            dt_str = format_schedule_time(s["dt"])
            duration = s.get("duration")
            if duration == 'Loading...' or duration == 0:
                dur_str = 'Loading...'
            elif duration:
                dur_str = MediaUtils.format_time(duration)
            else:
                dur_str = "Unknown"
            scheduled_by = f"<@{s['user']}>" if s.get('user') else "Unknown"
            embed.add_field(
                name=f"#{s['number']} — {s.get('title', 'Unknown')}",
                value=f"Scheduled for {dt_str}\nDuration: {dur_str}\nScheduled by: {scheduled_by}",
                inline=False
            )
        await ctx.send(embed=embed)

    @commands.command(name="unschedule")
    @commands.has_any_role(*Config.ALLOWED_ROLES)
    async def unschedule(self, ctx, number: int):
        """Remove all schedules for a given movie number."""
        before = len(self.scheduled)
        self.scheduled = [s for s in self.scheduled if s["number"] != number]
        self._save_schedule_backup()
        after = len(self.scheduled)
        if before == after:
            await ctx.send(f"No schedules found for movie #{number}.")
        else:
            await ctx.send(f"Removed all schedules for movie #{number}.")

    @tasks.loop(seconds=30)
    async def check_schedules(self):
        from src.config import Config
        now = datetime.now(PH_TZ)
        # Pre-announce 10 minutes before
        # Safeguard: ensure pre_announce_sent always exists
        if not hasattr(self, 'pre_announce_sent') or self.pre_announce_sent is None:
            self.pre_announce_sent = set()
        try:
            resolved = self._rebind_due_schedules(now)
        except Exception as e:
            logger.error(f"Error resolving scheduled items: {e}")
            resolved = {}
        for s in self.scheduled:
            pre_announce_key = (s['number'], s['dt'])
            if pre_announce_key not in self.pre_announce_sent:
                delta = (s['dt'] - now).total_seconds()
                if 0 < delta <= 600:  # 10 minutes
                    announce_ids = Config.get_announce_channel_ids()
                    channel_ids = set(announce_ids or [])
                    # Also notify the original scheduling channel for visibility
                    try:
                        if s.get('channel'):
                            channel_ids.add(s['channel'])
                    except Exception:
                        pass
                    role_id = getattr(Config, 'WATCH_ANNOUNCE_ROLE_ID', 0)
                    mention = f'<@&{role_id}>' if role_id else ''
                    for cid in channel_ids:
                        channel = self.bot.get_channel(cid)
                        if not channel:
                            try:
                                channel = await self.bot.fetch_channel(cid)
                            except Exception:
                                continue
                        try:
                            msg = f"{mention} ⏰ Reminder: Scheduled movie #{s['number']} ({s.get('title', 'Unknown')}) will start in 10 minutes!"
                            await channel.send(msg.strip())
                        except Exception:
                            pass
                    self.pre_announce_sent.add(pre_announce_key)
        # Run scheduled movies
        to_run = [s for s in self.scheduled if s["dt"] <= now]
        for s in to_run:
            try:
                idx, leaf = resolved.get(id(s), (None, None))
                if leaf is not None:
                    item_id = leaf.get('id')
                    self.vlc.play_item(item_id)
                    announce_ids = Config.get_announce_channel_ids()
                    channel_ids = set(announce_ids or [])
                    # Also notify the original scheduling channel for visibility
                    try:
                        if s.get('channel'):
                            channel_ids.add(s['channel'])
                    except Exception:
                        pass
                    # Include configured announce role mention (if any)
                    role_id = getattr(Config, 'WATCH_ANNOUNCE_ROLE_ID', 0)
                    mention = f'<@&{role_id}>' if role_id else ''
                    # Use .strip() so there is no leading space when mention is empty
                    msg = f"{mention} 🎬 Scheduled movie #{s['number']} ({s.get('title', 'Unknown')}) is now playing!".strip()
                    for cid in channel_ids:
                        channel = self.bot.get_channel(cid)
                        if not channel:
                            try:
                                channel = await self.bot.fetch_channel(cid)
                            except Exception:
                                channel = None
                        if channel:
                            try:
                                await channel.send(msg)
                            except Exception:
                                pass
                    # Try to fetch and send TMDB metadata embed
                    title = s.get('title', None)
                    year = None
                    # Try to extract year from title if present (e.g. "Movie Title (2020)")
                    import re
                    m = re.search(r"\((\d{4})\)$", title or "")
                    if m:
                        year = int(m.group(1))
                        title = title[:m.start()].strip()
                    if title:
                        embed = self.tmdb.get_movie_metadata(title, year, file_path=s.get('uri'))
                        if embed:
                            for cid in channel_ids:
                                channel = self.bot.get_channel(cid)
                                if not channel:
                                    try:
                                        channel = await self.bot.fetch_channel(cid)
                                    except Exception:
                                        channel = None
                                if channel:
                                    try:
                                        await channel.send(embed=embed)
                                    except Exception:
                                        pass
                else:
                    logger.warning(f"Scheduled movie #{s['number']} ({s.get('title')}) not found in playlist; no replacement match.")
            except Exception as e:
                logger.error(f"Error running scheduled movie: {e}")
        # Remove all that have run
        self.scheduled = [s for s in self.scheduled if s["dt"] > now]
        if to_run:
            self._save_schedule_backup()
