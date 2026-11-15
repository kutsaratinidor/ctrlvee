import asyncio
import logging
from urllib.parse import unquote

import discord
from discord.ext import commands, tasks

from ..config import Config
from ..utils.media_utils import MediaUtils
from .scheduler import _title_key

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
    """Title, link and poster from TMDB; empty values when there's no match, so it isn't retried."""
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


def _recent_embed(entry: dict) -> discord.Embed:
    viewers = sorted(entry["viewers"].values(), key=lambda v: -v["seconds"])
    watchers = ", ".join(f"{discord.utils.escape_markdown(v['name'])} · {v['seconds'] / 3600:.1f} h" for v in viewers)
    lines = [f"📺 Last episode **{entry['episode']}**"] if entry.get("episode") else []
    lines.append(f"👥 {watchers}")
    lines.append(f"🕒 <t:{entry['last']}:R>")
    embed = discord.Embed(
        title=entry.get("title") or entry["name"],
        url=entry.get("url"),
        description="\n".join(lines),
        color=discord.Color.blurple(),
    )
    if entry.get("poster"):
        embed.set_thumbnail(url=entry["poster"])
    return embed


def build_stats_embeds(stats) -> list[discord.Embed]:
    """Leaderboard, then one card per recently viewed title (Discord allows 10 embeds a message)."""
    embeds = [build_stats_embed(stats)]
    recent = stats.recently_viewed()
    if recent:
        embeds[0].set_footer(text="Recently viewed ↓")
        embeds += [_recent_embed(e) for e in recent]
    return embeds


class StatsCommands(commands.Cog):
    """Tracks who is in the voice room during playback and shows the leaderboard."""

    def __init__(self, bot: commands.Bot, vlc, stats, tmdb=None):
        self.bot = bot
        self.vlc = vlc
        self.stats = stats
        self.tmdb = tmdb

    async def cog_load(self):
        self._track.start()

    async def cog_unload(self):
        self._track.cancel()

    @tasks.loop(seconds=TICK_SECONDS)
    async def _track(self):
        try:
            channel = self.bot.get_channel(Config.VOICE_JOIN_CHANNEL_ID)
            if not isinstance(channel, discord.VoiceChannel):
                return
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
                # Look the poster up once, and only after it counts as viewed
                if entry and self.tmdb and "poster" not in entry and self.stats.is_viewed(entry):
                    try:
                        entry.update(await asyncio.to_thread(tmdb_card, self.tmdb, item))
                    except Exception:
                        # Retried next tick; a TMDB outage mustn't stop the save below
                        logger.exception("Poster lookup failed")
            self.stats.save()
        except Exception:
            # An unhandled error would stop the loop for the rest of the session
            logger.exception("Watch stats tick failed")

    @_track.before_loop
    async def _before_track(self):
        await self.bot.wait_until_ready()
        if not isinstance(self.bot.get_channel(Config.VOICE_JOIN_CHANNEL_ID), discord.VoiceChannel):
            logger.warning("Watch stats inactive: VOICE_JOIN_CHANNEL_ID is not a voice channel the bot can see")

    @commands.command(name="stats", aliases=["leaderboard"])
    @commands.guild_only()
    async def stats_cmd(self, ctx: commands.Context):
        """Show the watch leaderboard."""
        await ctx.send(embeds=build_stats_embeds(self.stats))
