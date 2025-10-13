import sys
import os
import asyncio
import logging
import threading
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
bot = commands.Bot(command_prefix=Config.DISCORD_COMMAND_PREFIX, intents=intents)

# Initialize services
from src.services.vlc_controller import VLCController
from src.services.tmdb_service import TMDBService
from src.services.watch_folder_service import WatchFolderService
from src.utils.media_utils import MediaUtils

vlc = VLCController()
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
                    shown = paths[:max_items]
                    remaining = len(paths) - len(shown)
                    title = f"📥 {len(paths)} new file(s) added to VLC playlist"
                    desc_lines = []
                    for p in shown:
                        try:
                            import os
                            name = os.path.basename(p)
                            pretty = MediaUtils.clean_filename_for_display(name)
                            icon = MediaUtils.get_media_icon(name)
                            desc_lines.append(f"• {icon} {pretty}")
                        except Exception as e:
                            logger.error(f"Error formatting announcement line for {p}: {e}")
                            try:
                                import os
                                desc_lines.append(f"• {os.path.basename(p)}")
                            except Exception:
                                desc_lines.append("• <new media>")
                    if remaining > 0:
                        desc_lines.append(f"… and {remaining} more")
                    embed = discord.Embed(title=title, description="\n".join(desc_lines), color=discord.Color.green())

                    # If exactly one item was added, try to fetch TMDB metadata and send an additional embed
                    tmdb_embed = None
                    try:
                        if len(paths) == 1 and tmdb_service:
                            import os
                            fname = os.path.basename(paths[0])
                            clean_title, year = MediaUtils.parse_movie_filename(fname)
                            if clean_title:
                                logger.info(f"Fetching TMDB metadata for single added item: '{clean_title}' year={year}")
                                tmdb_embed = tmdb_service.get_movie_metadata(clean_title, year)
                    except Exception as e:
                        logger.error(f"Failed to prepare TMDB embed for single-item announcement: {e}")

                    for ch in channels:
                        try:
                            logger.info(f"Sending announcement to channel {ch.id}")
                            await ch.send(embed=embed)
                            if tmdb_embed:
                                await ch.send(embed=tmdb_embed)
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
