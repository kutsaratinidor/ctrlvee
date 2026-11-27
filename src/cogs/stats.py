import asyncio
import io
import logging
import time
from datetime import datetime, time as dtime
from urllib.parse import unquote

import discord
from discord.ext import commands, tasks

from ..config import Config
from ..utils.media_utils import MediaUtils
from .scheduler import PH_TZ, _title_key

logger = logging.getLogger(__name__)

# ponytail: a 30s tick against 60s buckets misses minutes above 2x speed, so a title
# played faster can't reach "finished"; mark the whole span between ticks if that matters.
TICK_SECONDS = 30


def stats_title_key(item) -> str | None:
    """Stable key for a playlist item. The filename's title and year survive Radarr
    upgrades, and SxxEyy stays in the parsed title, so episodes count separately."""
    if item is None:
        return None
    key = _title_key(item.get('uri') or item.get('name'))
    return f"{key[0]}|{key[1] or ''}" if key else None


def view_info(item):
    """(key, name, episode label) for the recently viewed list. Episodes of one show
    and season share an entry, so a binge doesn't push everything else off the list."""
    raw = unquote(item.get('uri') or item.get('name') or '')
    tv_title, season, episode, _ = MediaUtils.parse_tv_filename(raw)
    if tv_title and episode:
        return f"tv|{tv_title.lower()}|{season}", tv_title, f"S{int(season or 0):02d}E{int(episode):02d}"
    title, year = MediaUtils.parse_movie_filename(raw)
    return stats_title_key(item), f"{title} ({year})" if year else (title or raw), None


def tmdb_card(tmdb, item) -> dict:
    """Title, link and poster from TMDB for a playlist leaf or a recent entry (both have
    uri/name); {"poster": None} when nothing matched."""
    raw = unquote(item.get('uri') or item.get('name') or '')
    embed, _ = tmdb.lookup_media_embed(raw, item.get('uri'))
    if not embed:
        return {"poster": None}
    return {"title": embed.title, "url": embed.url, "poster": embed.thumbnail.url}


def _format_ladder(rows: list, value) -> str:
    return "\n".join(
        f"{rank}. {discord.utils.escape_markdown(name)} — {value(amount)}"
        for rank, (name, amount) in enumerate(rows, 1)
    )


def build_stats_embed(stats) -> discord.Embed:
    hours, finished = stats.ladders()
    embed = discord.Embed(title="🏆 Watch Leaderboard", color=discord.Color.gold())
    if not hours:
        embed.description = "No watch time recorded yet."
        return embed
    embed.add_field(name="Hours watched", value=_format_ladder(hours, lambda s: f"{s / 3600:.1f} h"), inline=False)
    embed.add_field(
        name="Titles finished",
        value=_format_ladder(finished, str) or "Nobody has finished a title yet.",
        inline=False,
    )
    return embed


# Fallback when the grid image can't be made: Discord merges up to 4 embeds that share a
# url into one card with an image grid. That's client behaviour, not documented API.
GALLERY_URL = "https://www.themoviedb.org/"
GALLERY_SIZE = 4
GRID_FILENAME = "recently-watched.jpg"

# Last rendered grid: (tiles, jpeg). /stats reuses it until the list or a poster changes.
_grid_cache: list = [None, None]


def _recent_line(rank: int, entry: dict) -> str:
    title = discord.utils.escape_markdown(entry.get("title") or entry["name"])
    if entry.get("url"):
        title = f"[{title}]({entry['url']})"
    episode = f" · {entry['episode']}" if entry.get("episode") else ""
    return f"**{rank}.** {title}{episode}"


def grid_cells() -> int:
    """Titles in the Recently Watched card: STATS_POSTER_GRID_MAX squared."""
    return Config.STATS_POSTER_GRID_MAX ** 2


def _render_grid(tiles: tuple) -> bytes:
    if _grid_cache[0] == tiles:
        return _grid_cache[1]
    from ..utils import poster_grid  # Pillow; if it's missing, /stats falls back to the gallery
    jpeg, complete = poster_grid.render(list(tiles))
    # Keep a grid with a failed download only for this reply, so the next /stats tries again
    _grid_cache[:] = [tiles, jpeg] if complete else [None, None]
    return jpeg


async def build_stats_message(stats) -> dict:
    """Keyword arguments for send(): the leaderboard, then one "Recently Watched" card with
    the titles over a poster grid image. Who watched and for how long is the leaderboard's job."""
    board = build_stats_embed(stats)
    recent = stats.recently_viewed(limit=grid_cells())
    if not recent:
        return {"embeds": [board]}
    card = discord.Embed(
        title="🎬 Recently Watched",
        url=GALLERY_URL,
        description="\n".join(_recent_line(rank, e) for rank, e in enumerate(recent, 1)),
        color=discord.Color.blurple(),
    )
    if not any(e.get("poster") for e in recent):
        return {"embeds": [board, card]}
    tiles = tuple((e.get("title") or e["name"], e.get("poster")) for e in recent)
    try:
        jpeg = await asyncio.to_thread(_render_grid, tiles)
    except Exception:
        logger.exception("Poster grid failed; using Discord's 4-poster gallery instead")
        posters = [e["poster"] for e in recent[:GALLERY_SIZE] if e.get("poster")]
        card.set_image(url=posters[0])
        return {"embeds": [board, card] + [discord.Embed(url=GALLERY_URL).set_image(url=p) for p in posters[1:]]}
    card.set_image(url=f"attachment://{GRID_FILENAME}")
    return {"embeds": [board, card], "file": discord.File(io.BytesIO(jpeg), filename=GRID_FILENAME)}


def announce_time() -> dtime | None:
    """STATS_ANNOUNCE_TIME as a Philippines-time time of day, or None when it's off or invalid."""
    raw = Config.STATS_ANNOUNCE_TIME
    if raw.lower() in {"", "off", "0", "false", "no"}:
        return None
    try:
        return datetime.strptime(raw, "%H:%M").time().replace(tzinfo=PH_TZ)
    except ValueError:
        logger.warning(f"STATS_ANNOUNCE_TIME={raw!r} isn't HH:MM; the daily stats post is off")
        return None


def _announce_text() -> str:
    command = "`/stats`" if Config.ENABLE_SLASH_COMMANDS else f"`{Config.DISCORD_COMMAND_PREFIX}stats`"
    return f"📊 **Daily watch stats** · run {command} anytime to see the latest"


class StatsCommands(commands.Cog):
    """Tracks who is in the voice room during playback and shows the leaderboard."""

    def __init__(self, bot: commands.Bot, vlc, stats, tmdb=None):
        self.bot = bot
        self.vlc = vlc
        self.stats = stats
        self.tmdb = tmdb
        self._empty_since = None  # monotonic time the room last went watcher-free

    async def cog_load(self):
        self._track.start()
        when = announce_time()
        if when:
            # A fixed time of day rather than an interval, so restarts don't move it
            self._announce.change_interval(time=when)
            self._announce.start()
            logger.info(f"Daily stats post at {when.strftime('%H:%M')} PH time")

    async def cog_unload(self):
        self._track.cancel()
        self._announce.cancel()

    @tasks.loop(hours=24)  # replaced by the configured time of day in cog_load
    async def _announce(self):
        """Post the leaderboard to the watch announce channels, once there's anything on it."""
        if not self.stats.ladders()[0]:
            return
        for channel_id in Config.get_announce_channel_ids():
            try:
                channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
                # Built per channel: an attached file can only be sent once (the grid image is cached)
                await channel.send(content=_announce_text(), **await build_stats_message(self.stats))
                logger.info(f"Posted daily stats to channel {channel_id}")
            except Exception:
                # One bad channel mustn't stop the others, or end the loop
                logger.exception(f"Daily stats post to channel {channel_id} failed")

    @_announce.before_loop
    async def _before_announce(self):
        await self.bot.wait_until_ready()

    @tasks.loop(seconds=TICK_SECONDS)
    async def _track(self):
        try:
            channel = self.bot.get_channel(Config.VOICE_JOIN_CHANNEL_ID)
            if not isinstance(channel, discord.VoiceChannel):
                return
            await self._idle_shuffle(channel)
            viewers = {m.id: m.display_name for m in channel.members if not m.bot}
            if not viewers:
                return
            status = await asyncio.to_thread(self.vlc.get_status)
            if status is None or status.findtext('state') != 'playing':
                return
            playlist = await asyncio.to_thread(self.vlc.get_playlist)
            leaves = playlist.findall('.//leaf') if playlist is not None else []
            item = next((leaf for leaf in leaves if leaf.get('current')), None)
            self.stats.record(
                viewers,
                stats_title_key(item),
                int(status.findtext('time') or 0),
                int(status.findtext('length') or 0),
                TICK_SECONDS,
            )
            if item is not None:
                entry = self.stats.record_view(*view_info(item), viewers, TICK_SECONDS)
                if entry:
                    entry["uri"] = item.get('uri')  # so a later poster retry can look it up
            if self.tmdb:
                await self._fill_poster()
            self.stats.save()
        except Exception:
            # An unhandled error would stop the loop for the rest of the session
            logger.exception("Watch stats tick failed")

    async def _idle_shuffle(self, channel):
        """Turn shuffle back on once nobody but bots and the streaming account has been in
        the room for IDLE_SHUFFLE_MINUTES, e.g. after a series binge left it off."""
        if Config.IDLE_SHUFFLE_MINUTES <= 0:
            return
        if any(not m.bot and m.id not in Config.STATS_EXCLUDED_USER_IDS for m in channel.members):
            self._empty_since = None
            return
        now = time.monotonic()
        if self._empty_since is None:
            self._empty_since = now
        if now - self._empty_since < Config.IDLE_SHUFFLE_MINUTES * 60:
            return
        if self.vlc._queued_items:  # soft queue turned shuffle off and restores it itself
            return
        self._empty_since = now  # check again after another idle stretch, not every tick
        status = await asyncio.to_thread(self.vlc.get_status)
        if status is None or status.findtext('random') == 'true':
            return
        if await asyncio.to_thread(self.vlc.toggle_shuffle) is None:
            return
        logger.info("Voice room idle; shuffle turned back on")
        for channel_id in (Config.COMMAND_CHANNEL_IDS or Config.get_announce_channel_ids())[:1]:
            target = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
            await target.send("💤 No one's watching the stream, so shuffle is back on.")

    async def _fill_poster(self):
        """At most one TMDB lookup a tick, for the first title in the grid still missing its poster."""
        now = time.time()
        entry = self.stats.poster_due(grid_cells(), now)
        if entry is None:
            return
        entry["poster_checked"] = int(now)
        try:
            entry.update(await asyncio.to_thread(tmdb_card, self.tmdb, entry))
        except Exception:
            # Counts as an empty result and is retried later; it mustn't stop the save
            logger.exception("Poster lookup failed")
            entry.setdefault("poster", None)

    @_track.before_loop
    async def _before_track(self):
        await self.bot.wait_until_ready()
        if not isinstance(self.bot.get_channel(Config.VOICE_JOIN_CHANNEL_ID), discord.VoiceChannel):
            logger.warning("Watch stats inactive: VOICE_JOIN_CHANNEL_ID is not a voice channel the bot can see")

    @commands.command(name="stats", aliases=["leaderboard"])
    @commands.guild_only()
    async def stats_cmd(self, ctx: commands.Context):
        """Show the watch leaderboard."""
        async with ctx.typing():
            message = await build_stats_message(self.stats)
        await ctx.send(**message)
