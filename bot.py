import sys
import asyncio
import logging
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
bot = commands.Bot(command_prefix='!', intents=intents)

# Initialize services
from src.services.vlc_controller import VLCController
from src.services.tmdb_service import TMDBService
from src.services.watch_folder_service import WatchFolderService
from src.utils.media_utils import MediaUtils

vlc = VLCController()
tmdb_service = TMDBService()
watch_service = WatchFolderService(vlc)

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
        # Set announcement notifier if channel configured
        if Config.WATCH_ANNOUNCE_CHANNEL_ID:
            channel = bot.get_channel(Config.WATCH_ANNOUNCE_CHANNEL_ID)
            if not channel:
                try:
                    channel = await bot.fetch_channel(Config.WATCH_ANNOUNCE_CHANNEL_ID)
                except Exception:
                    channel = None

            def notifier(paths):
                # Dispatch into the asyncio loop to send messages
                async def _send_announcement():
                    if not channel:
                        return
                    # Batch and truncate
                    max_items = max(1, Config.WATCH_ANNOUNCE_MAX_ITEMS)
                    shown = paths[:max_items]
                    remaining = len(paths) - len(shown)
                    title = f"📥 {len(paths)} new file(s) added to VLC playlist"
                    # Format without revealing file paths: show icon + cleaned title only
                    desc_lines = []
                    for p in shown:
                        try:
                            import os
                            name = os.path.basename(p)
                            pretty = MediaUtils.clean_filename_for_display(name)
                            icon = MediaUtils.get_media_icon(name)
                            desc_lines.append(f"• {icon} {pretty}")
                        except Exception:
                            # Fallback to just the basename on any unexpected error
                            try:
                                import os
                                desc_lines.append(f"• {os.path.basename(p)}")
                            except Exception:
                                desc_lines.append("• <new media>")
                    if remaining > 0:
                        desc_lines.append(f"… and {remaining} more")
                    embed = discord.Embed(title=title, description="\n".join(desc_lines), color=discord.Color.green())
                    try:
                        await channel.send(embed=embed)
                    except discord.Forbidden:
                        logger.warning("Missing permission to send announcements in the configured channel.")
                    except Exception as e:
                        logger.error(f"Failed to send announcement: {e}")

                asyncio.run_coroutine_threadsafe(_send_announcement(), bot.loop)

            watch_service.set_notifier(notifier)

        started = watch_service.start()
        if started:
            logger.info("WatchFolderService started")
        else:
            logger.info("WatchFolderService not started (disabled or already running)")
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
        user_roles = ", ".join(f"'{role.name}'" for role in ctx.author.roles if role.name != "@everyone")
        logger.warning("Role check failed:")
        logger.warning(f"- User has roles: {user_roles}")
        logger.warning(f"- Required roles (any of): {allowed_roles}")
        await ctx.send(f"You need one of these roles to use this command: {allowed_roles}\nYou have these roles: {user_roles}")
    elif isinstance(error, commands.CommandNotFound):
        await ctx.send(f"Command not found. Use `!controls` to see available commands.")
    else:
        logger.error(f"Command error: {str(error)}")
        logger.error(f"Error type: {type(error)}")
        await ctx.send(f"Error: {str(error)}")

@bot.command()
async def controls(ctx):
    """Show all available VLC controls"""
    try:
        embed = discord.Embed(
            title="VLC Bot Help",
            description="Control VLC media player through Discord!",
            color=discord.Color.blue()
        )

        # Basic Playback Controls
        playback_commands = """
`!play` - Start or resume playback
`!pause` - Pause playback
`!stop` - Stop playback
`!restart` - Restart current file from the beginning
`!next` - Play next track
`!previous` - Play previous track
`!rewind [seconds]` - Rewind by specified seconds (default: 10)
`!forward [seconds]` - Fast forward by specified seconds (default: 10)
`!shuffle` - Toggle shuffle mode on/off
`!shuffle_on` - Enable shuffle mode
`!shuffle_off` - Disable shuffle mode
        """
        embed.add_field(name="🎮 Playback Controls", value=playback_commands, inline=False)

        # Playlist Management
        playlist_commands = """
`!list` - Show playlist with interactive navigation
`!search <query>` - Search for items in playlist
`!play_search <query>` - Search and play a specific item
`!play_num <number>` - Play item by its number in playlist
        """
        embed.add_field(name="📋 Playlist Management", value=playlist_commands, inline=False)

        # Queue Management
        queue_commands = """
`!queue_next <number>` - Queue a playlist item to play next (shows item title & positions)
`!queue_status` - Show current queue with item titles and playlist positions
`!clear_queue` - Clear all queue tracking
        """
        embed.add_field(name="📑 Queue Management", value=queue_commands, inline=False)

        # Status & Scheduling
        status_commands = """
`!status` - Show current VLC status (state, volume, playing item)
`!schedule <number> <YYYY-MM-DD> <HH:MM>` - Schedule a movie by playlist number (Philippines time)
`!schedules` - List all upcoming scheduled movies
`!unschedule <number>` - Remove all schedules for a movie number
        """
        embed.add_field(name="ℹ️ Status & Scheduling", value=status_commands, inline=False)

        # Add footer note about permissions
        roles_str = ", ".join(f"'{role}'" for role in Config.ALLOWED_ROLES)
        embed.set_footer(text=f"⚠️ Most commands require one of these roles: {roles_str}")

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
