

import discord
from discord.ext import commands, tasks
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import asyncio
import logging
import json
import os
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
logger = logging.getLogger(__name__)


SCHEDULE_BACKUP_FILE = "schedule_backup.json"


from src.services.tmdb_service import TMDBService

class Scheduler(commands.Cog):
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

    @commands.command(name="schedule")
    async def schedule_movie(self, ctx, number: int, date: str, time: str):
        """Schedule a movie by playlist number and PH time. Usage: !schedule <number> <YYYY-MM-DD> <HH:MM>"""
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
                    await ctx.send(f"❌ Movie #{number} is already scheduled at {s['dt'].strftime('%Y-%m-%d %H:%M %Z')}. Double scheduling is not allowed.")
                    return
            # Get movie title and duration from playlist (on demand)
            playlist = self.vlc.get_playlist()
            items = playlist.findall('.//leaf') if playlist is not None else []
            idx = number - 1
            if 0 <= idx < len(items):
                filename = items[idx].get('name', 'Unknown')
                title = MediaUtils.clean_movie_title(filename)
                # Try to get duration (in seconds) from attribute or child
                duration = None
                dur_attr = items[idx].get('duration')
                if dur_attr and dur_attr.isdigit():
                    duration = int(dur_attr)
                else:
                    dur_elem = items[idx].find('duration')
                    if dur_elem is not None and dur_elem.text and dur_elem.text.isdigit():
                        duration = int(dur_elem.text)
            else:
                title = "Unknown"
                duration = None
            entry = {
                "number": number,
                "title": title,
                "dt": dt,
                "user": ctx.author.id,
                "channel": ctx.channel.id,
                "duration": duration
            }
            self.scheduled.append(entry)
            self._save_schedule_backup()
            dur_str = MediaUtils.format_time(duration) if duration else "?"
            embed = discord.Embed(
                title="Movie Scheduled",
                color=discord.Color.green()
            )
            embed.add_field(name="Number", value=f"#{number}", inline=True)
            embed.add_field(name="Title", value=title, inline=True)
            embed.add_field(name="Scheduled For", value=dt.strftime('%Y-%m-%d %H:%M %Z'), inline=False)
            embed.add_field(name="Duration", value=dur_str, inline=True)
            await ctx.send(embed=embed)
        except Exception as e:
            await ctx.send(f"❌ Invalid date/time format. Use: !schedule <number> <YYYY-MM-DD> <HH:MM>")

    @commands.command(name="schedules")
    async def list_schedules(self, ctx):
        """List all upcoming scheduled movies."""
        if not self.scheduled:
            await ctx.send("No movies scheduled.")
            return
        embed = discord.Embed(title="Upcoming Scheduled Movies", color=discord.Color.purple())
        for s in sorted(self.scheduled, key=lambda x: x["dt"]):
            dt_str = s["dt"].strftime('%Y-%m-%d %H:%M %Z') if isinstance(s["dt"], datetime) else str(s["dt"])
            dur_str = MediaUtils.format_time(s.get("duration")) if s.get("duration") else "?"
            embed.add_field(
                name=f"#{s['number']} — {s.get('title', 'Unknown')}",
                value=f"Scheduled for {dt_str}\nDuration: {dur_str}",
                inline=False
            )
        await ctx.send(embed=embed)

    @commands.command(name="unschedule")
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
        now = datetime.now(PH_TZ)
        to_run = [s for s in self.scheduled if s["dt"] <= now]
        for s in to_run:
            try:
                # Play by number (1-based index)
                playlist = self.vlc.get_playlist()
                items = playlist.findall('.//leaf') if playlist is not None else []
                idx = s["number"] - 1
                if 0 <= idx < len(items):
                    item_id = items[idx].get('id')
                    self.vlc.play_item(item_id)
                    channel = self.bot.get_channel(s["channel"])
                    if channel:
                        msg = f"🎬 Scheduled movie #{s['number']} ({s.get('title', 'Unknown')}) is now playing!"
                        await channel.send(msg)
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
                            embed = self.tmdb.get_movie_metadata(title, year)
                            if embed:
                                await channel.send(embed=embed)
                else:
                    logger.warning(f"Scheduled movie number {s['number']} not found in playlist.")
            except Exception as e:
                logger.error(f"Error running scheduled movie: {e}")
        # Remove all that have run
        self.scheduled = [s for s in self.scheduled if s["dt"] > now]
        if to_run:
            self._save_schedule_backup()
