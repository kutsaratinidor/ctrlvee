import sys
import os
import asyncio
import logging
import threading
import re
import time
from src.config import Config
from discord.ext import commands
import discord

# Get logger for this module
logger = logging.getLogger(__name__)

# Validate configuration
config_errors = Config.validate()
if config_errors:
    logger.error("Configuration Errors:")
    for error in config_errors:
        logger.error(f"- {error}")
    logger.error("Please fix these errors in your .env file and try again.")
    sys.exit(1)

# Log current configuration
Config.print_config()

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True  # Needed for voice events
intents.guilds = True        # Needed for channel resolution
bot = commands.Bot(command_prefix=Config.DISCORD_COMMAND_PREFIX, intents=intents)

# Initialize services
from src.services.vlc_controller import VLCController
from src.services.tmdb_service import TMDBService
from src.services.watch_folder_service import WatchFolderService
from src.utils.media_utils import MediaUtils

vlc = VLCController(bot=bot)
tmdb_service = TMDBService()
watch_service = WatchFolderService(vlc)

# Optional: background playlist autosave
_autosave_thread = None
_autosave_stop = threading.Event()

# Import cogs
from src.cogs.playback import PlaybackCommands
from src.cogs.playlist import PlaylistCommands
from src.cogs.scheduler import Scheduler
from src.version import __version__

# -------- Voice connection management --------
# Reconnect guard variables (configurable)
_last_voice_disconnect_ts = 0.0
_reconnect_attempts = 0
_MAX_RECONNECTS = int(getattr(Config, 'VOICE_MAX_RECONNECTS', 3))
_RECONNECT_WINDOW = int(getattr(Config, 'VOICE_RECONNECT_WINDOW', 60))  # seconds
_RECONNECT_COOLDOWN = int(getattr(Config, 'VOICE_RECONNECT_COOLDOWN', 30))  # seconds

# Voice connect constants (configurable)
_VOICE_CONNECT_TIMEOUT = float(getattr(Config, 'VOICE_CONNECT_TIMEOUT', 20.0))
_VOICE_CONNECT_RETRY_DELAY = float(getattr(Config, 'VOICE_CONNECT_RETRY_DELAY', 2.0))
_VOICE_ERROR_RETRY_DELAY = float(getattr(Config, 'VOICE_ERROR_RETRY_DELAY', 5.0))

# Known Discord voice error codes
_VOICE_ERROR_CODES = {
    4006: "Session invalidated - server may be having issues",
    4009: "Session timed out",
    4014: "Disconnected due to channel being deleted or moved",
    4015: "Server missed last heartbeat",
}

# Serialize voice join attempts to avoid overlapping connects
_voice_join_lock = asyncio.Lock()

async def _resolve_voice_channel() -> discord.VoiceChannel | None:
    """Resolve and validate the configured voice channel."""
    try:
        if not getattr(Config, 'ENABLE_VOICE_JOIN', False):
            return None
        channel_id = getattr(Config, 'VOICE_JOIN_CHANNEL_ID', 0)
        if not isinstance(channel_id, int) or channel_id <= 0:
            logger.warning("Voice join enabled but VOICE_JOIN_CHANNEL_ID is not configured or invalid")
            return None

        ch = bot.get_channel(channel_id)
        if not ch:
            try:
                ch = await bot.fetch_channel(channel_id)
            except Exception as e:
                logger.warning(f"Failed to fetch voice channel {channel_id}: {e}")
                return None
        if not isinstance(ch, discord.VoiceChannel):
            logger.warning(f"Configured channel {channel_id} is not a voice channel")
            return None

        # Permission check
        perms = ch.permissions_for(ch.guild.me)
        if not perms.connect:
            logger.warning(f"Missing CONNECT permission in voice channel '{ch.name}'")
            return None
        if not perms.speak:
            logger.info(f"No SPEAK permission in '{ch.name}' (ok if presence-only)")

        return ch
    except Exception as e:
        logger.warning(f"Error resolving voice channel: {e}")
        return None

async def join_voice_channel():
    """Join the configured voice channel with retries and verification."""
    if not getattr(Config, 'ENABLE_VOICE_JOIN', False) or not getattr(Config, 'VOICE_AUTO_JOIN_ON_START', True):
        logger.info("Voice auto-join is disabled by configuration")
        return

    async with _voice_join_lock:
        ch = await _resolve_voice_channel()
        if not ch:
            return

        guild = ch.guild

        # If already connected correctly, do nothing
        existing = discord.utils.get(bot.voice_clients, guild=guild)
        if existing and existing.is_connected() and getattr(existing, 'channel', None) and existing.channel.id == ch.id:
            logger.info(f"Already connected to voice channel: {ch.name}")
            return

        # If connected to a different channel in the same guild, try moving first
        if existing and existing.is_connected() and getattr(existing, 'channel', None) and existing.channel.id != ch.id:
            try:
                logger.info(f"Moving voice client from '{existing.channel.name}' to '{ch.name}'")
                await existing.move_to(ch)
                await asyncio.sleep(1)
                if existing.channel and existing.channel.id == ch.id:
                    logger.info("Voice client moved to configured channel successfully")
                    return
            except Exception as e:
                logger.warning(f"Failed to move voice client; will reconnect: {e}")

        # Clean up any stale client in the same guild
        if existing:
            try:
                await existing.disconnect(force=True)
                await asyncio.sleep(1)
            except Exception:
                pass

        retries = max(0, int(getattr(Config, 'VOICE_INITIAL_RETRIES', 2)))
        for attempt in range(retries + 1):
            # Abort further attempts if we detect a good connection
            existing = discord.utils.get(bot.voice_clients, guild=guild)
            if existing and existing.is_connected() and getattr(existing, 'channel', None) and existing.channel.id == ch.id:
                logger.info("Detected active connection to target channel; stopping retries")
                return

            if attempt > 0:
                logger.info(f"Waiting {_VOICE_CONNECT_RETRY_DELAY}s before retry...")
                await asyncio.sleep(_VOICE_CONNECT_RETRY_DELAY)

            logger.info(f"Attempting to connect to voice channel: {ch.name} ({ch.id}) [attempt {attempt+1}/{retries+1}]")
            try:
                # Sanity: ensure channel still exists
                try:
                    await guild.fetch_channel(ch.id)
                except Exception as e:
                    logger.warning(f"Channel verification failed before connect: {e}")
                    continue

                vc = await ch.connect(timeout=_VOICE_CONNECT_TIMEOUT, self_mute=True, self_deaf=True)
                if vc and vc.is_connected():
                    if vc.channel and vc.channel.id == ch.id:
                        # small grace to stabilize
                        await asyncio.sleep(1)
                        if vc.is_connected():
                            logger.info(f"Successfully joined voice channel: {ch.name}")
                            return
                        else:
                            logger.warning("Voice connection dropped immediately after connect; retrying")
                            continue
                    else:
                        logger.warning(f"Connected to unexpected channel (got {getattr(vc.channel,'id',None)}, expected {ch.id}); disconnecting and retrying")
                        try:
                            await vc.disconnect(force=True)
                        except Exception:
                            pass
                        continue

                logger.warning("Voice connection unclear/failed; will retry")
            except discord.ClientException as ce:
                if "already connected to a voice channel" in str(ce).lower():
                    existing = discord.utils.get(bot.voice_clients, guild=guild)
                    if existing and existing.is_connected():
                        logger.info("Detected existing active voice connection; keeping it")
                        return
                    logger.info("Detected stale voice connection; cleaning up and retrying")
                    try:
                        if existing:
                            await existing.disconnect(force=True)
                    except Exception:
                        pass
                    continue
                logger.warning(f"Voice client error: {ce}")
            except discord.ConnectionClosed as cc:
                code = getattr(cc, 'code', None)
                msg = _VOICE_ERROR_CODES.get(code, 'Unknown error')
                logger.warning(f"Voice WebSocket closed with code {code} ({msg})")
                # 4006 (session invalid) and 4009 (timeout) are recoverable with delay
                if code in (4006, 4009):
                    await asyncio.sleep(_VOICE_ERROR_RETRY_DELAY)
                    continue
            except Exception as e:
                logger.warning(f"Voice connect attempt failed: {type(e).__name__}: {e}")
                # Backoff before next retry
                await asyncio.sleep(_VOICE_ERROR_RETRY_DELAY)

        logger.warning("Voice connection attempts exhausted; will rely on reconnection handler")


@bot.event
async def on_voice_state_update(member, before, after):
    """When the bot itself gets disconnected from voice, attempt a controlled reconnect."""
    global _last_voice_disconnect_ts, _reconnect_attempts

    try:
        if not getattr(Config, 'ENABLE_VOICE_JOIN', False):
            return
        if not bot.user or member.id != bot.user.id:
            return

        # We only care when we end up not in a channel anymore
        if after.channel is not None:
            return

        now = time.time()

        # cooldown check
        if now - _last_voice_disconnect_ts < _RECONNECT_COOLDOWN:
            logger.warning("Reconnection attempt blocked - in cooldown period")
            return

        # windowed attempts
        if now - _last_voice_disconnect_ts > _RECONNECT_WINDOW:
            _reconnect_attempts = 0

        _reconnect_attempts += 1
        if _reconnect_attempts > _MAX_RECONNECTS:
            logger.warning("Too many reconnection attempts - entering cooldown")
            _last_voice_disconnect_ts = now
            return

        _last_voice_disconnect_ts = now
        logger.info(f"Bot was disconnected from voice. Attempting reconnect... (Attempt {_reconnect_attempts}/{_MAX_RECONNECTS})")

        # Clean up any existing client in this guild
        try:
            if before and before.channel:
                existing = discord.utils.get(bot.voice_clients, guild=before.channel.guild)
                if existing:
                    await existing.disconnect(force=True)
                    await asyncio.sleep(1)
        except Exception:
            pass

        # Try to rejoin the configured channel
        await join_voice_channel()
    except Exception as e:
        logger.warning(f"Error in voice reconnection handler: {e}")
@bot.event
async def setup_hook():
    """This is called when the bot is starting up"""
    logger.info("Setting up bot...")
    try:
        # Add cogs
        await bot.add_cog(PlaybackCommands(bot, vlc, tmdb_service, watch_service))
        await bot.add_cog(PlaylistCommands(bot, vlc, tmdb_service, watch_service))
        await bot.add_cog(Scheduler(bot, vlc))
        logger.info("Cogs loaded successfully")
    except Exception as e:
        logger.error(f"Error loading cogs: {e}")
        sys.exit(1)

@bot.event
async def on_ready():
    # Log voice join/reconnect configuration to confirm loaded values
    try:
        logger.info(
            "Voice join config: enabled=%s, auto_on_start=%s, channel_id=%s",
            getattr(Config, 'ENABLE_VOICE_JOIN', False),
            getattr(Config, 'VOICE_AUTO_JOIN_ON_START', True),
            getattr(Config, 'VOICE_JOIN_CHANNEL_ID', 0),
        )
        logger.info(
            "Voice reconnect config: max=%s, window=%ss, cooldown=%ss",
            _MAX_RECONNECTS,
            _RECONNECT_WINDOW,
            _RECONNECT_COOLDOWN,
        )
        logger.info(
            "Voice connect config: timeout=%ss, retry_delay=%ss, error_retry_delay=%ss, initial_retries=%s",
            _VOICE_CONNECT_TIMEOUT,
            _VOICE_CONNECT_RETRY_DELAY,
            _VOICE_ERROR_RETRY_DELAY,
            getattr(Config, 'VOICE_INITIAL_RETRIES', 2),
        )
    except Exception:
        pass
    async def send_startup_announcement():
        announce_ids = Config.get_announce_channel_ids()
        if not announce_ids:
            return
        embed = discord.Embed(
            title="🤖 CtrlVee Bot is Online!",
            description=(
                f"Version: {__version__}\n"
                f"Command prefix: `{Config.DISCORD_COMMAND_PREFIX}`\n"
                "Ready to receive commands."
            ),
            color=discord.Color.green()
        )
        embed.set_footer(text="Thank you for using CtrlVee!")
        # If a Ko-fi URL is configured, make it clearly visible and clickable
        try:
            if Config.KOFI_URL:
                # Set the embed URL so the title becomes clickable
                try:
                    embed.url = Config.KOFI_URL
                except Exception:
                    pass

                # Add a visible field with the clickable link (angle brackets ensure a clean URL)
                try:
                    embed.add_field(name="Support CtrlVee", value=f"☕ {f'<{Config.KOFI_URL}>'}", inline=False)
                except Exception:
                    pass


        except Exception:
            # Non-fatal: don't block startup announcement on footer issues
            pass
        # Prepare local avatar attachment if available
        avatar_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'screenshots', 'avatar.png')
        has_avatar_file = os.path.exists(avatar_path)
        if has_avatar_file:
            try:
                embed.set_thumbnail(url='attachment://avatar.png')
            except Exception:
                pass

        for cid in announce_ids:
            channel = bot.get_channel(cid)
            if not channel:
                try:
                    channel = await bot.fetch_channel(cid)
                except Exception as e:
                    logger.warning(f"Could not resolve announce channel {cid}: {e}")
                    continue
            try:
                if has_avatar_file:
                    # Send the avatar image as an attachment so embed thumbnail displays
                    await channel.send(embed=embed, file=discord.File(avatar_path, filename='avatar.png'))
                else:
                    await channel.send(embed=embed)
                logger.info(f"Sent startup message to channel {cid}")
            except Exception as e:
                logger.warning(f"Failed to send startup message to channel {cid}: {e}")

    # If initial enqueue on start is enabled, delay the announcement until after initial scan completes
    if Config.WATCH_ENQUEUE_ON_START and watch_service:
        logger.info("Delaying startup announcement until watch folder initial scan completes...")
        loop = asyncio.get_event_loop()
        def wait_and_announce():
            try:
                # Wait up to 2 minutes for the initial scan to finish
                done = watch_service.wait_initial_scan_done(timeout=120)
                logger.info(f"Initial scan completion wait result: {done}")
            except Exception as e:
                logger.error(f"Error while waiting for initial scan: {e}")
            finally:
                asyncio.run_coroutine_threadsafe(send_startup_announcement(), loop)
        # Run the waiting in a lightweight thread so we don't block the event loop
        threading.Thread(target=wait_and_announce, name="AnnounceAfterInitialScan", daemon=True).start()
    else:
        await send_startup_announcement()
    """Called when the bot is ready"""
    logger.info(f'{bot.user} has connected to Discord!')
    
    # Log all loaded commands and their checks
    logger.info("Loaded commands:")
    for command in bot.commands:
        logger.info(f"Command: {command.name}")
        if command.checks:
            logger.info(f"  Checks: {[check.__name__ if hasattr(check, '__name__') else str(check) for check in command.checks]}")
        if hasattr(command, 'cog_name'):
            logger.info(f"  Cog: {command.cog_name}")
    
    # Test VLC connection
    logger.info("Testing VLC connection...")
    status = vlc.get_status()
    if status is not None:
        state = status.find('state').text
        logger.info(f"Successfully connected to VLC's HTTP interface (Current state: {state})")
    else:
        logger.warning("Could not connect to VLC. Please make sure VLC is running with the HTTP interface enabled")
        logger.info("\nTo enable VLC HTTP interface:")
        logger.info("1. Open VLC")
        logger.info("2. Go to Preferences (Cmd+,)")
        logger.info("3. Click 'Show All' at the bottom left")
        logger.info("4. Go to Interface → Main Interfaces")
        logger.info("5. Check 'Web'")
        logger.info("6. Go to Interface → Main Interfaces → Lua")
        logger.info("7. Set password as 'vlc'")
        logger.info("8. Restart VLC")
        logger.warning("Starting bot anyway - will retry connection when needed...")

    # If presence updates are globally disabled, clear any existing presence immediately
    try:
        if not getattr(Config, 'ENABLE_PRESENCE', True):
            try:
                await bot.change_presence(activity=None)
                logger.info("Presence cleared at startup because ENABLE_PRESENCE=false")
            except Exception as e:
                logger.debug(f"Failed to clear presence at startup: {e}")
    except Exception:
        # Guard: any failure here is non-fatal
        pass

    # Start watch service if configured
    try:
        # Set announcement notifier for multiple channels if configured
        ids = Config.get_announce_channel_ids()
        logger.info(f"Configured announce channel IDs: {list(ids) if ids else 'None'}")
        if ids:
            async def get_channels():
                channels = []
                for cid in Config.get_announce_channel_ids():
                    ch = bot.get_channel(cid)
                    logger.debug(f"Attempting to resolve channel ID {cid}: bot.get_channel -> {ch}")
                    if not ch:
                        try:
                            ch = await bot.fetch_channel(cid)
                            logger.debug(f"Fetched channel {cid} via fetch_channel: {ch}")
                        except Exception as e:
                            logger.error(f"Failed to fetch channel {cid}: {e}")
                            ch = None
                    if ch:
                        channels.append(ch)
                logger.info(f"Resolved announce channels: {[ch.id for ch in channels]}")
                return channels

            def notifier(paths):
                logger.info(f"Notifier called with {len(paths)} new files: {paths}")
                async def _send_announcement():
                    channels = await get_channels()
                    logger.info(f"Announcing to channels: {[ch.id for ch in channels]}")
                    if not channels:
                        logger.warning("No announce channels resolved. Announcement skipped.")
                        return
                    max_items = max(1, Config.WATCH_ANNOUNCE_MAX_ITEMS)
                    # Helper: detect a season number from filenames or parent folder
                    def _detect_season(paths_list):
                        season = None
                        parent = None
                        for p in paths_list:
                            try:
                                bn = os.path.basename(p)
                                par = os.path.dirname(p)
                                # Look for S01E02 or s01e02
                                m = re.search(r'[sS]?(\d{1,2})[xXeE](\d{1,2})', bn)
                                if m:
                                    season = int(m.group(1))
                                    parent = par
                                    break
                                # 1x02 pattern
                                m2 = re.search(r'(?<!\d)(\d{1,2})[xX](\d{1,2})(?!\d)', bn)
                                if m2:
                                    season = int(m2.group(1))
                                    parent = par
                                    break
                                # Parent folder named 'Season 1' or similar
                                m3 = re.search(r'[sS]eason[\s_\-]?(\d{1,2})', par)
                                if m3:
                                    season = int(m3.group(1))
                                    parent = par
                                    break
                            except Exception:
                                continue
                        return season, parent

                    shown = paths[:max_items]
                    remaining = len(paths) - len(shown)

                    tmdb_embed = None
                    # Multi-episode batch: try to create a compact season summary
                    if len(paths) > 1:
                        season_num, season_parent = _detect_season(paths)
                        if season_num is not None:
                            title = f"📥 Added Season {season_num} — {len(paths)} episode(s)"
                        else:
                            title = f"📥 {len(paths)} new file(s) added to VLC playlist"

                        desc_lines = []
                        for p in shown:
                            try:
                                name = os.path.basename(p)
                                pretty = MediaUtils.clean_filename_for_display(name)
                                icon = MediaUtils.get_media_icon(name)
                                desc_lines.append(f"• {icon} {pretty}")
                            except Exception as e:
                                logger.error(f"Error formatting announcement line for {p}: {e}")
                                try:
                                    desc_lines.append(f"• {os.path.basename(p)}")
                                except Exception:
                                    desc_lines.append("• <new media>")

                        if remaining > 0:
                            desc_lines.append(f"… and {remaining} more")

                        embed = discord.Embed(title=title, description="\n".join(desc_lines), color=discord.Color.green())
                        # Add Support/Kofi field when configured
                        try:
                            if Config.KOFI_URL:
                                embed.add_field(name="Support CtrlVee", value=f"☕ {f'<{Config.KOFI_URL}>'}", inline=False)
                        except Exception:
                            pass
                        # If we detected a season number and TMDB is available, fetch TV/season embed
                        tv_embed = None
                        try:
                            if season_num is not None and tmdb_service:
                                # Try to derive a series title from the first path's folder or filename
                                # Prefer parent folder name (likely the series title)
                                series_name = None
                                try:
                                    series_name = os.path.basename(season_parent) if season_parent else None
                                except Exception:
                                    series_name = None
                                # Fallback to cleaning filename
                                if not series_name and paths:
                                    try:
                                        series_name = MediaUtils.clean_movie_title(os.path.basename(paths[0]))
                                    except Exception:
                                        series_name = None

                                if series_name:
                                    tv_embed = tmdb_service.get_tv_metadata(series_name, season_num)
                        except Exception as e:
                            logger.debug(f"TV metadata lookup failed: {e}")

                    else:
                        # Single item: keep previous behavior and attempt TMDB metadata
                        title = f"📥 {len(paths)} new file(s) added to VLC playlist"
                        desc_lines = []
                        for p in shown:
                            try:
                                name = os.path.basename(p)
                                pretty = MediaUtils.clean_filename_for_display(name)
                                icon = MediaUtils.get_media_icon(name)
                                desc_lines.append(f"• {icon} {pretty}")
                            except Exception as e:
                                logger.error(f"Error formatting announcement line for {p}: {e}")
                                try:
                                    desc_lines.append(f"• {os.path.basename(p)}")
                                except Exception:
                                    desc_lines.append("• <new media>")

                        embed = discord.Embed(title=title, description="\n".join(desc_lines), color=discord.Color.green())

                        try:
                            if len(paths) == 1 and tmdb_service:
                                fname = os.path.basename(paths[0])
                                clean_title, year = MediaUtils.parse_movie_filename(fname)
                                if clean_title:
                                    logger.info(f"Fetching TMDB metadata for single added item: '{clean_title}' year={year}")
                                    tmdb_embed = tmdb_service.get_movie_metadata(clean_title, year)
                        except Exception as e:
                            logger.error(f"Failed to prepare TMDB embed for single-item announcement: {e}")

                    # Try to get TMDB metadata and create a single rich embed
                    final_embed = None
                    try:
                        clean_title, year = MediaUtils.parse_movie_filename(name)
                        tmdb_embed = tmdb_service.get_movie_metadata(clean_title, year)
                        if not tmdb_embed:
                            # If movie fails, try TV
                            tmdb_embed = tmdb_service.get_tv_metadata(clean_title)
                        
                        if tmdb_embed:
                            # Use the rich embed, but adjust title and description for the announcement
                            tmdb_embed.title = f"✨ New Media Added: {tmdb_embed.title}"
                            tmdb_embed.description = (
                                f"**{name}** has been added to the library.\n\n"
                                f"{tmdb_embed.description or ''}"
                            ).strip()
                            tmdb_embed.color = discord.Color.purple()
                            final_embed = tmdb_embed
                        
                    except Exception as e:
                        logger.error(f"Error getting TMDB data for new file: {e}")

                    # If no rich embed, create a simple one
                    if not final_embed:
                        final_embed = discord.Embed(
                            title="✨ New Media Added",
                            description=f"**{name}** has been added to the library.",
                            color=discord.Color.purple()
                        )

                    # Send the announcement to all configured channels
                    for ch in channels:
                        try:
                            logger.info(f"Sending announcement to channel {ch.id}")
                            await ch.send(embed=final_embed)
                        except discord.Forbidden:
                            logger.warning(f"Missing permission to send announcements in channel {ch.id}.")
                        except Exception as e:
                            logger.error(f"Failed to send announcement to channel {ch.id}: {e}")
                asyncio.run_coroutine_threadsafe(_send_announcement(), bot.loop)

            watch_service.set_notifier(notifier)

        started = watch_service.start()
        if started:
            logger.info("WatchFolderService started")
        else:
            logger.info("WatchFolderService not started (disabled or already running)")

        # Start autosave thread if configured
        if Config.PLAYLIST_AUTOSAVE_FILE:
            def _resolve_autosave_path(filename: str) -> str:
                if os.path.isabs(filename):
                    return filename
                # Save to same directory as bot.py
                base_dir = os.path.dirname(os.path.abspath(__file__))
                # __file__ is in src/, go up one level to project root
                project_root = os.path.abspath(os.path.join(base_dir, os.pardir, os.pardir))
                return os.path.join(project_root, filename)

            autosave_path = _resolve_autosave_path(Config.PLAYLIST_AUTOSAVE_FILE)
            interval = max(10, int(Config.PLAYLIST_AUTOSAVE_INTERVAL))

            def autosave_worker():
                logger.info(f"Playlist autosave enabled -> file='{autosave_path}', interval={interval}s")
                while not _autosave_stop.is_set():
                    try:
                        # Verify VLC is reachable and playlist has entries before saving
                        status = vlc.get_status()
                        if status is None:
                            logger.debug("Playlist autosave skipped: VLC HTTP interface not reachable")
                        else:
                            # Check playlist entries
                            playlist_xml = vlc.get_playlist()
                            has_entries = False
                            try:
                                if playlist_xml is not None:
                                    leaves = playlist_xml.findall('.//leaf')
                                    if leaves and len(leaves) > 0:
                                        has_entries = True
                            except Exception:
                                # If parsing playlist fails, be conservative and skip saving
                                has_entries = False

                            if not has_entries:
                                logger.debug("Playlist autosave skipped: playlist is empty or has no entries")
                            else:
                                if autosave_path.lower().endswith('.xspf'):
                                    xspf = vlc.export_playlist_xspf()
                                    if xspf:
                                        logger.info(f"Saving playlist (XSPF) -> {autosave_path}")
                                        with open(autosave_path, 'w', encoding='utf-8') as f:
                                            f.write(xspf)
                                        logger.debug(f"Playlist autosaved (XSPF) to {autosave_path}")
                                    else:
                                        logger.debug("Playlist autosave skipped (no XSPF data returned)")
                                else:
                                    data = vlc.export_playlist()
                                    if data:
                                        logger.info(f"Saving playlist (JSON) -> {autosave_path}")
                                        with open(autosave_path, 'w', encoding='utf-8') as f:
                                            import json
                                            json.dump({
                                                'saved_at': __import__('time').time(),
                                                'items': data
                                            }, f, indent=2)
                                        try:
                                            item_count = len(data) if hasattr(data, '__len__') else 'unknown'
                                        except Exception:
                                            item_count = 'unknown'
                                        logger.debug(f"Playlist autosaved to {autosave_path} ({item_count} items)")
                                    else:
                                        logger.debug("Playlist autosave skipped (no playlist data returned)")
                    except Exception as e:
                        logger.error(f"Playlist autosave error: {e}")
                    finally:
                        _autosave_stop.wait(interval)

            global _autosave_thread
            if not _autosave_thread or not _autosave_thread.is_alive():
                _autosave_stop.clear()
                _autosave_thread = threading.Thread(target=autosave_worker, name="PlaylistAutosave", daemon=True)
                _autosave_thread.start()

        # Join voice channel after all startup tasks are done
        await join_voice_channel()
    except Exception as e:
        logger.error(f"Failed to start WatchFolderService: {e}")

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return
    
    logger.debug(f'Received message: {message.content}')
    
    # Only log roles for guild messages where author is a Member (has roles)
    if hasattr(message, 'guild') and message.guild is not None and hasattr(message.author, 'roles'):
        logger.debug(f'User roles: {[role.name for role in message.author.roles if role.name != "@everyone"]}')
        logger.debug(f'Required roles: {Config.ALLOWED_ROLES}')
    else:
        if hasattr(message, 'guild') and message.guild is not None:
            logger.debug('Message received in guild but author has no roles (User object)')
        else:
            logger.debug('Message received outside of a guild (DM or system message)')
        
    await bot.process_commands(message)

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingAnyRole):
        allowed_roles = ", ".join(f"'{role}'" for role in Config.ALLOWED_ROLES)
        logger.warning(f"Role check failed: required roles (any of): {allowed_roles}")
        await ctx.send(f"You need one of these roles to use this command: {allowed_roles}")
    elif isinstance(error, commands.CommandNotFound):
        await ctx.send(f"Command not found. Use `{Config.DISCORD_COMMAND_PREFIX}controls` to see available commands.")
    else:
        logger.error(f"Command error: {str(error)}")
        logger.error(f"Error type: {type(error)}")
        await ctx.send(f"Error: {str(error)}")

@bot.command()
async def controls(ctx):
    """Show all available VLC controls"""
    try:
        prefix = Config.DISCORD_COMMAND_PREFIX
        embed = discord.Embed(
            title="VLC Bot Help",
            description=f"Control VLC media player through Discord!\n\n**Current command prefix:** `{prefix}`\nUse `{prefix}controls` to show this help.",
            color=discord.Color.blue()
        )

        # Basic Playback Controls
        playback_commands = f"""
`{prefix}play` - Start or resume playback
`{prefix}pause` - Pause playback
`{prefix}stop` - Stop playback
`{prefix}restart` - Restart current file from the beginning
`{prefix}next` - Play next track
`{prefix}previous` - Play previous track
`{prefix}rewind [seconds]` - Rewind by specified seconds (default: 10)
`{prefix}forward [seconds]` - Fast forward by specified seconds (default: 10)
`{prefix}shuffle` - Toggle shuffle mode on/off
`{prefix}shuffle_on` - Enable shuffle mode
`{prefix}shuffle_off` - Disable shuffle mode
`{prefix}speed <rate|preset>` - Set playback speed (examples: `1.5`, `1.25`, or presets like `1.5x`); aliases: `spd`, `speed15`, `speednorm`
`{prefix}speedstatus` - Show current playback rate (alias: `spdstatus`)
    """
        embed.add_field(name="🎮 Playback Controls", value=playback_commands, inline=False)

        # Playlist Management
        playlist_commands = f"""
`{prefix}list` - Show playlist with interactive navigation
`{prefix}search <query>` - Search for items in playlist
`{prefix}play_search <query>` - Search and play a specific item
`{prefix}play_num <number>` - Play item by its number in playlist
        """
        embed.add_field(name="📋 Playlist Management", value=playlist_commands, inline=False)

        # Queue Management
        queue_commands = f"""
`{prefix}queue_next <number>` - Queue a playlist item to play next (shows item title & positions)
`{prefix}queue_status` - Show current queue with item titles and playlist positions
`{prefix}clear_queue` - Clear all queue tracking
`{prefix}remove_queue <N|#N>` - Remove from queue by queue order (N) or playlist number (#N)
        """
        embed.add_field(name="📑 Queue Management", value=queue_commands, inline=False)

        # Status & Scheduling
        status_commands = f"""
`{prefix}status` - Show current VLC status (state, volume, playing item)
`{prefix}schedule <number> <YYYY-MM-DD> <HH:MM>` - Schedule a movie by playlist number (Philippines time)
`{prefix}schedules` - List all upcoming scheduled movies
`{prefix}unschedule <number>` - Remove all schedules for a movie number
        """
        embed.add_field(name="ℹ️ Status & Scheduling", value=status_commands, inline=False)

        # Subtitles
        subtitles_commands = f"""
`{prefix}sub_list` - List available subtitle tracks and show which one is selected
`{prefix}sub_set <id|#index|off>` - Select subtitles by VLC track ID, list index (e.g., `#2`), or disable with `off`
`{prefix}sub_next` / `{prefix}sub_prev` - Cycle to next/previous subtitle track (when supported by VLC)

Tip: Use `{prefix}sub_list` first, then `{prefix}sub_set #2` to pick by list index, or `{prefix}sub_set off` to disable.
        """
        embed.add_field(name="💬 Subtitles", value=subtitles_commands, inline=False)

        # Add footer note about permissions
        roles_str = ", ".join(f"'{role}'" for role in Config.ALLOWED_ROLES)
        footer_text = f"⚠️ Most commands require one of these roles: {roles_str}"
        embed.set_footer(text=footer_text)

        # Make embed more visible: try to use local avatar image as attachment thumbnail; fall back to bot avatar
        sent_file = None
        avatar_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'screenshots', 'avatar.png')
        try:
            if os.path.exists(avatar_path):
                sent_file = discord.File(avatar_path, filename='avatar.png')
                embed.set_thumbnail(url='attachment://avatar.png')
            else:
                # Fallback to bot avatar if available
                if ctx.bot.user and getattr(ctx.bot.user, 'display_avatar', None):
                    embed.set_thumbnail(url=ctx.bot.user.display_avatar.url)
        except Exception:
            try:
                if ctx.bot.user and getattr(ctx.bot.user, 'display_avatar', None):
                    embed.set_thumbnail(url=ctx.bot.user.display_avatar.url)
            except Exception:
                pass

        # Add a clickable Ko-fi link as a field (angle brackets make it clickable in Discord)
        if Config.KOFI_URL:
            try:
                embed.add_field(name="Support CtrlVee", value=f"☕ {f'<{Config.KOFI_URL}>'}", inline=False)
            except Exception:
                # Non-fatal if link rendering fails
                pass

        # Send with attachment if available
        if sent_file:
            await ctx.send(embed=embed, file=sent_file)
        else:
            await ctx.send(embed=embed)
    except discord.Forbidden:
        await ctx.send("❌ I need the 'Embed Links' permission to show the help message.")
    except Exception as e:
        await ctx.send(f"An error occurred: {str(e)}")
@bot.command(name="version")
async def version(ctx):
    """Show the bot version and basic configuration info"""
    embed = discord.Embed(
        title="CtrlVee Version",
        color=discord.Color.blue()
    )
    embed.add_field(name="Version", value=__version__, inline=True)
    embed.add_field(name="Items Per Page", value=str(Config.ITEMS_PER_PAGE), inline=True)
    embed.add_field(name="TMDB", value=("Configured" if Config.TMDB_API_KEY else "Not Configured"), inline=True)
    await ctx.send(embed=embed)

def main():
    """Main entry point for the bot"""
    try:
        # Run the bot
        bot.run(Config.DISCORD_TOKEN)
    except Exception as e:
        logger.critical(f"Error starting bot: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
