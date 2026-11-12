import asyncio
import logging

import discord
from discord.ext import commands, tasks

from ..config import Config
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


class StatsCommands(commands.Cog):
    """Tracks who is in the voice room during playback and shows the leaderboard."""

    def __init__(self, bot: commands.Bot, vlc, stats):
        self.bot = bot
        self.vlc = vlc
        self.stats = stats

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
        await ctx.send(embed=build_stats_embed(self.stats))
