import discord
from discord.ext import commands
import asyncio
import logging
import xml.etree.ElementTree as ET
from ..utils.media_utils import MediaUtils
from ..config import Config
from ..utils.command_utils import format_cmd, format_cmd_inline

# Set up logger for this module
logger = logging.getLogger(__name__)

class PlaybackCommands(commands.Cog):
    def __init__(self, bot, vlc_controller, tmdb_service, watch_service):
        self.bot = bot
        self.vlc = vlc_controller
        self.tmdb = tmdb_service
        self.watch_service = watch_service
        self.last_state_change = {}
        self.logger = logging.getLogger(__name__)
        self.last_known_state = None
        self.last_known_position = None
        self.last_known_playing_item = None  # Track the last item that was playing
        self.monitoring_task = None
        self._presence_progress_task = None
        self._last_command_announce_ts = 0.0
        self._suppress_auto_announce_until = 0.0
        self._last_announced_item_id = None
        self._last_announced_item_name = None
        self._command_initiated_change = False  # Suppress auto announce immediately after bot-issued next/prev
        # Unified announcer cooldown
        self._last_now_playing_key = None
        self._last_now_playing_ts = 0.0
        try:
            from ..config import Config
            self._np_cooldown = float(getattr(Config, 'NOW_PLAYING_COOLDOWN_SECONDS', 10.0))
            self._auto_suppress_seconds = float(getattr(Config, 'AUTO_ANNOUNCE_SUPPRESS_SECONDS', 6.0))
        except Exception:
            self._np_cooldown = 10.0
            self._auto_suppress_seconds = 6.0
        self.periodic_announce_task = None
        self.playback_started_event = asyncio.Event()
        self.last_queue_auto_play = 0  # Timestamp of last queue auto-play to prevent rapid triggers
        # Presence/update throttling for bot activity updates
        self._presence_last_set = 0.0
        self._presence_last_name = None  # Track last presence activity name to avoid throttling new titles
        # Allow a configurable throttle via Config.PRESENCE_UPDATE_THROTTLE (seconds); default to 5s
        try:
            self._presence_throttle_seconds = int(getattr(Config, 'PRESENCE_UPDATE_THROTTLE', 5))
        except Exception:
            self._presence_throttle_seconds = 5
        
        # Track selected subtitle since VLC API doesn't expose it
        self.selected_subtitle_stream_index = None
        self._initial_scan_pending = True # Guard for startup presence
        
    def signal_initial_scan_complete(self):
        """Signal that the initial watch folder scan is complete."""
        self.logger.info("Initial scan complete; presence clearing is now enabled.")
        self._initial_scan_pending = False

    async def cog_load(self):
        """Called when the cog is loaded"""
        self.monitoring_task = self.bot.loop.create_task(self._monitor_vlc_state())
        self.logger.info("VLC state monitoring started")
        # Schedule a one-time startup presence sync (runs after the bot is ready)
        try:
            self.bot.loop.create_task(self._startup_presence_sync())
        except Exception as e:
            self.logger.debug(f"Could not schedule startup presence sync: {e}")
        # Start periodic presence progress updater if enabled
        try:
            self._presence_progress_task = self.bot.loop.create_task(self._presence_progress_loop())
            self.logger.info("Presence progress updater started")
        except Exception as e:
            self.logger.debug(f"Could not start presence progress updater: {e}")
        # Start periodic announcement task
        try:
            self.periodic_announce_task = self.bot.loop.create_task(self._periodic_announce_loop())
            self.logger.info("Periodic announcement task started")
        except Exception as e:
            self.logger.debug(f"Could not start periodic announcement task: {e}")

    async def _startup_presence_sync(self):
        """Sync bot presence on startup if VLC is already playing/paused.

        Runs after the bot is ready to ensure presence updates are applied.
        Respects Config.ENABLE_PRESENCE and uses a brief delay to let VLC status stabilize.
        """
        try:
            if not getattr(Config, 'ENABLE_PRESENCE', True):
                return
            # Ensure the bot is fully ready before attempting presence updates
            await self.bot.wait_until_ready()
            # Small delay to allow VLC HTTP status to stabilize after connect
            await asyncio.sleep(0.4)

            status = self.vlc.get_status()
            if status is None:
                return

            state_elem = status.find('state')
            current_state = state_elem.text if state_elem is not None else None
            if current_state not in ['playing', 'paused']:
                return

            # Try to resolve the current item's display name from playlist first
            name = None
            try:
                playlist = self.vlc.get_playlist()
                if playlist is not None:
                    _, current_item = self._find_current_position(playlist)
                    if current_item is not None:
                        name = current_item.get('name')
            except Exception:
                name = None

            # Fallback: pull filename from status information
            if not name:
                try:
                    info_root = status.find('information')
                    if info_root is not None:
                        for category in info_root.findall('category'):
                            for info in category.findall('info'):
                                if info.get('name') == 'filename':
                                    name = info.text
                                    break
                            if name:
                                break
                except Exception:
                    name = None

            if name:
                try:
                    await self._set_presence(name, reason="startup sync")
                    self.logger.info(f"Startup presence set: {name}")
                except Exception as e:
                    self.logger.debug(f"Failed to set startup presence: {e}")
        except Exception as e:
            self.logger.debug(f"Startup presence sync skipped: {e}")

    async def _announce_now_playing(self, origin: str, item: ET.Element | None, position: int | None):
        """Unified Now Playing announcer with cooldown and de-duplication.

        origin: 'command' | 'monitor' | 'periodic'
        item: current VLC playlist leaf element
        position: 1-based playlist position if known
        """
        try:
            if item is None:
                return
            name = item.get('name') or ''
            if not name:
                return
            # Build a de-duplication key from cleaned name and position
            key = f"{MediaUtils.clean_filename_for_display(name)}|{position or ''}"
            now_ts = asyncio.get_event_loop().time()
            # Cooldown: avoid re-announcing the same item too frequently
            if self._last_now_playing_key == key and (now_ts - self._last_now_playing_ts) < self._np_cooldown:
                return
            # Prepare TMDB embed if possible
            tmdb_embed = None
            try:
                title, year = MediaUtils.parse_movie_filename(name)
                tmdb_embed = self.tmdb.get_movie_metadata(title, year)
                if not tmdb_embed:
                    tmdb_embed = self.tmdb.get_tv_metadata(title)
            except Exception:
                tmdb_embed = None
            if tmdb_embed:
                final = tmdb_embed
                final.title = f"Now Playing: {final.title}"
            else:
                final = discord.Embed(title=f"Now Playing: {name}", color=discord.Color.blue())
            # Add position if available
            if position:
                try:
                    final.add_field(name="Playlist", value=f"#{position}", inline=True)
                except Exception:
                    pass
            # Update presence
            try:
                await self._set_presence(name, reason=f"now playing ({origin})")
            except Exception:
                pass
            # Send to announce channels
            channel_ids = Config.get_announce_channel_ids()
            for channel_id in channel_ids or []:
                try:
                    channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
                    if channel:
                        await channel.send(embed=final)
                        self.logger.info(f"Sent unified Now Playing ({origin}) to channel {channel_id}")
                except Exception as e:
                    self.logger.error(f"Failed to send unified Now Playing to channel {channel_id}: {e}")
            # Record last
            self._last_now_playing_key = key
            self._last_now_playing_ts = now_ts
            # Mark command suppression states
            try:
                self._last_command_announce_ts = now_ts if origin == 'command' else self._last_command_announce_ts
                if origin == 'command':
                    self._suppress_auto_announce_until = now_ts + self._auto_suppress_seconds
                    self._last_announced_item_id = item.get('id')
                    self._last_announced_item_name = name
            except Exception:
                pass
        except Exception as e:
            self.logger.error(f"_announce_now_playing error: {e}")
        
    async def cog_unload(self):
        """Called when the cog is unloaded"""
        if self.monitoring_task:
            self.monitoring_task.cancel()
            self.logger.info("VLC state monitoring stopped")
        if self._presence_progress_task:
            self._presence_progress_task.cancel()
            self.logger.info("Presence progress updater stopped")
        if self.periodic_announce_task:
            self.periodic_announce_task.cancel()
            self.logger.info("Periodic announcement task stopped")

    @commands.command(name='cleanup', aliases=['plcleanup','cleanup_missing'])
    async def cleanup_missing(self, ctx: commands.Context):
        """Remove missing/unavailable files from the VLC playlist.

        Scans the current VLC playlist for items whose underlying files no longer exist
        (e.g., replaced/upgraded files) and removes them. Shows a summary of removed items.
        """
        try:
            await ctx.trigger_typing()
        except Exception:
            pass
        try:
            result = self.vlc.remove_missing_playlist_items()
            removed = int(result.get('removed', 0))
            items = result.get('items', []) or []
            if removed == 0:
                await ctx.send("✅ Playlist cleanup: no missing files detected.")
                return
            max_list = 10
            listed = items[:max_list]
            more = removed - len(listed)
            lines = []
            for it in listed:
                nm = it.get('name') or '<unknown>'
                lines.append(f"• {MediaUtils.clean_filename_for_display(nm)}")
            if more > 0:
                lines.append(f"… and {more} more")
            embed = discord.Embed(
                title="🧹 Playlist Cleanup",
                description=f"Removed {removed} missing file(s) from the playlist:\n\n" + "\n".join(lines),
                color=discord.Color.orange()
            )
            try:
                embed.set_footer(text="Cleanup tool")
            except Exception:
                pass
            await ctx.send(embed=embed)
        except Exception as e:
            self.logger.error(f"cleanup_missing error: {e}")
            await ctx.send(f"❌ Cleanup failed: {e}")

    async def _periodic_announce_loop(self):
        """Periodically announce the currently playing media, triggered by playback start."""
        try:
            await self.bot.wait_until_ready()
        except Exception:
            return

        while not self.bot.is_closed():
            try:
                # Wait until playback starts
                self.logger.info("Periodic announcer is waiting for playback to start...")
                await self.playback_started_event.wait()
                self.logger.info("Playback started, periodic announcer is active.")

                interval = int(getattr(Config, 'PERIODIC_ANNOUNCE_INTERVAL', 300))

                # Wait for the initial interval before the first announcement
                await asyncio.sleep(interval)

                # Keep announcing as long as playback is active
                while self.playback_started_event.is_set():
                    if not getattr(Config, 'PERIODIC_ANNOUNCE_ENABLED', False):
                        self.logger.debug("Periodic announcement disabled in config, pausing until re-enabled.")
                        # If disabled, wait until it might be re-enabled without spamming checks
                        await asyncio.sleep(interval)
                        continue

                    channel_ids = Config.get_announce_channel_ids()
                    if not channel_ids:
                        self.logger.debug("Periodic announcement skipped: No announcement channels configured.")
                        await asyncio.sleep(interval)
                        continue
                    
                    status = self.vlc.get_status()
                    if status and status.find('state').text == 'playing':
                        self.logger.info("VLC is playing, preparing periodic announcement...")
                        embed = await self.get_status_embed()
                        if embed:
                            for channel_id in channel_ids:
                                try:
                                    channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
                                    if channel:
                                        await channel.send(embed=embed)
                                        self.logger.info(f"Sent periodic 'Now Playing' announcement to channel {channel_id}")
                                except Exception as e:
                                    self.logger.error(f"Failed to send periodic announcement to channel {channel_id}: {e}")
                        else:
                            self.logger.debug("Periodic announcement skipped: could not generate status embed.")
                    else:
                        self.logger.debug("Periodic announcement skipped: VLC not in 'playing' state.")

                    # Wait for the next interval
                    await asyncio.sleep(interval)

            except asyncio.CancelledError:
                break # Exit loop cleanly on cancellation
            except Exception as e:
                self.logger.error(f"Error in periodic announcement loop: {e}")
                # If a major error occurs, wait a bit before restarting the wait
                await asyncio.sleep(30)

    async def _presence_progress_loop(self):
        """Periodically update presence with playback progress (mm:ss/MM:SS)."""
        try:
            await self.bot.wait_until_ready()
        except Exception:
            return
        interval = 0
        try:
            interval = max(5, int(getattr(Config, 'PRESENCE_PROGRESS_UPDATE_INTERVAL', 30)))
        except Exception:
            interval = 30
        while not self.bot.is_closed():
            try:
                # Respect config toggles
                if not getattr(Config, 'ENABLE_PRESENCE', True) or not getattr(Config, 'ENABLE_PRESENCE_PROGRESS', True):
                    await asyncio.sleep(interval)
                    continue

                status = None
                try:
                    status = self.vlc.get_status()
                except Exception:
                    status = None

                if status is None:
                    await asyncio.sleep(interval)
                    continue

                state_elem = status.find('state')
                current_state = state_elem.text if state_elem is not None else None
                # Only update progress while playing or paused
                if current_state not in ['playing', 'paused']:
                    await asyncio.sleep(interval)
                    continue

                # Resolve current title
                title = None
                try:
                    playlist = self.vlc.get_playlist()
                    if playlist is not None:
                        _, current_item = self._find_current_position(playlist)
                        if current_item is not None:
                            title = current_item.get('name')
                except Exception:
                    title = None
                if not title:
                    try:
                        info_root = status.find('information')
                        if info_root is not None:
                            for category in info_root.findall('category'):
                                for info in category.findall('info'):
                                    if info.get('name') == 'filename':
                                        title = info.text
                                        break
                                if title:
                                    break
                    except Exception:
                        title = None

                if not title:
                    await asyncio.sleep(interval)
                    continue

                # Compute progress string
                progress_suffix = None
                try:
                    time_elem = status.find('time')
                    length_elem = status.find('length')
                    if time_elem is not None and length_elem is not None and time_elem.text and length_elem.text:
                        cur = int(time_elem.text)
                        total = int(length_elem.text)
                        if total > 0 and cur >= 0:
                            def fmt(n: int) -> str:
                                return f"{n//60}:{n%60:02d}"
                            progress_suffix = f"{fmt(cur)}/{fmt(total)}"
                except Exception:
                    progress_suffix = None

                name_for_presence = title
                if progress_suffix:
                    # Trim to keep final presence text within safe limits (~120 chars)
                    base = title
                    try:
                        suffix = f" — {progress_suffix}"
                        max_total = 120
                        max_base = max_total - len(suffix)
                        if len(base) > max_base:
                            base = base[: max(0, max_base - 3)] + "..."
                        name_for_presence = base + suffix
                    except Exception:
                        name_for_presence = f"{title} — {progress_suffix}"

                # Mark paused state in reason for clarity (no change to visible text)
                reason = "progress tick (paused)" if current_state == 'paused' else "progress tick"
                try:
                    await self._set_presence(name_for_presence, reason=reason)
                except Exception:
                    pass

            except Exception as e:
                logger.debug(f"Presence progress loop error: {e}")
            finally:
                try:
                    await asyncio.sleep(interval)
                except Exception:
                    pass
        
    def _find_current_position(self, playlist):
        """Find the position of the current item in the playlist
        
        Args:
            playlist: The XML playlist from VLC
            
        Returns:
            tuple: (position, current_item) where position is 1-based index or None if not found
                  and current_item is the XML element or None if not found
        """
        if playlist is None:
            return None, None
            
        current_item = None
        position = None
        
        # Find current item and its position
        for i, item in enumerate(playlist.findall('.//leaf')):
            if item.get('current'):
                current_item = item
                position = i + 1  # Convert to 1-based index
                break
                
        return position, current_item

    async def _check_cooldown(self, ctx):
        """Check if enough time has passed since last state change"""
        guild_id = str(ctx.guild.id) if ctx.guild else 'dm'
        current_time = asyncio.get_event_loop().time()
        
        if guild_id in self.last_state_change:
            time_since_last = current_time - self.last_state_change[guild_id]
            if time_since_last < 1:
                logger.debug(f"Command ignored - too soon after last state change ({time_since_last:.2f}s)")
                return False
        return True
    
    def _check_queue_auto_play_cooldown(self):
        """Check if enough time has passed since last queue auto-play to prevent rapid triggers"""
        current_time = asyncio.get_event_loop().time()
        time_since_last = current_time - self.last_queue_auto_play
        
        if time_since_last < 2.0:  # 2 second cooldown between queue auto-plays
            return False
        
        self.last_queue_auto_play = current_time
        return True
        
    @commands.command(name='speed', aliases=['spd', 'speed15', 'speednorm'])
    @commands.has_any_role(*Config.ALLOWED_ROLES)
    async def speed(self, ctx, target: str = None):
        """Set playback speed. Usage examples: set a numeric rate (e.g. 1.5) or use a preset like 'normal'.

        Short aliases are available (e.g. `speed15`, `speednorm`).
        The playback rate will also be reset automatically when a file finishes playing.
        """
        try:
            # No target provided -> show usage embed
            if target is None:
                embed = discord.Embed(
                    title="Playback Speed — Usage",
                    description=(
                        f"Set the playback rate using a numeric value, or use a preset like 'normal' to reset.\n\n"
                        f"Examples: {format_cmd_inline('speed 1.5')} or {format_cmd_inline('speed normal')}"
                    ),
                    color=discord.Color.blue()
                )
                embed.add_field(name="Aliases", value="spd, speed15, speednorm", inline=True)
                await ctx.send(embed=embed)
                return

            t = target.strip().lower()
            if t in ('1.5', '1.5x', '15', 'fast', 'up'):
                rate = 1.5
            elif t in ('1', '1.0', 'normal', 'default', 'reset', 'norm'):
                rate = 1.0
            else:
                # Try to parse a float
                try:
                    rate = float(t.rstrip('x'))
                except Exception:
                    embed = discord.Embed(
                        title="Invalid speed",
                        description="Please provide a numeric rate like `1.5` or use `normal` to reset.",
                        color=discord.Color.red()
                    )
                    embed.set_footer(text=f"Usage: {format_cmd_inline('speed 1.5')}")
                    await ctx.send(embed=embed)
                    return

            ok = False
            try:
                ok = self.vlc.set_rate(rate)
            except Exception as e:
                logger.error(f"Error setting playback rate: {e}")

            if ok:
                if rate == 1.0:
                    embed = discord.Embed(
                        title="Playback Speed Reset",
                        description="✅ Playback speed reset to normal (1.0x)",
                        color=discord.Color.green()
                    )
                else:
                    embed = discord.Embed(
                        title="Playback Speed Updated",
                        description=f"✅ Playback speed set to {rate}x",
                        color=discord.Color.green()
                    )
                # Add Ko-fi support field when configured
                try:
                    if Config.KOFI_URL:
                        embed.add_field(name="Support kutsaratinidor by supporting CtrlVee", value=f"☕ {f'<{Config.KOFI_URL}>'}", inline=False)
                except Exception:
                    pass
                await ctx.send(embed=embed)
            else:
                embed = discord.Embed(
                    title="Playback Speed Failed",
                    description=f"⚠️ Failed to set playback speed to {rate}x",
                    color=discord.Color.orange()
                )
                await ctx.send(embed=embed)
        except Exception as e:
            logger.error(f"Speed command error: {e}")
            embed = discord.Embed(
                title="Error",
                description=f"An error occurred while setting playback speed: {e}",
                color=discord.Color.dark_red()
            )
            await ctx.send(embed=embed)
        
    async def _monitor_vlc_state(self):
        """Background task to monitor VLC state changes"""
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                status = self.vlc.get_status()
                if status:
                    current_state = status.find('state').text
                    # If VLC is stopped, clear the bot's presence (throttled)
                    # BUT: do not clear it if we are still waiting for the initial scan to complete
                    try:
                        if current_state == 'stopped':
                            if not self._initial_scan_pending:
                                await self._set_presence(None, reason="stopped")
                            # Signal that playback has stopped
                            if self.playback_started_event.is_set():
                                self.logger.info("Playback stopped, deactivating periodic announcer.")
                                self.playback_started_event.clear()
                        elif current_state == 'playing':
                            # Signal that playback has started
                            if not self.playback_started_event.is_set():
                                self.playback_started_event.set()
                    except Exception:
                        # Non-fatal: presence/event update failures should not stop monitoring
                        pass
                    
                    # Get current position and item from playlist
                    playlist = self.vlc.get_playlist()
                    current_position = None
                    current_item = None
                    if playlist is not None:
                        current_position, current_item = self._find_current_position(playlist)
                    
                    # Check for state changes
                    if self.last_known_state is not None:
                        state_changed = current_state != self.last_known_state
                        position_changed = current_position != self.last_known_position
                        
                        # Handle queue transitions when track changes OR when state changes to stopped/paused
                        if (position_changed and current_item is not None) or (state_changed and current_state in ['stopped', 'paused']):
                            current_item_id = current_item.get('id') if current_item else None
                            
                            # Priority 1: Handle position changes (track transitions)
                            if position_changed and current_item_id:
                                # Check if there's a queue and this is a natural track progression
                                next_queued = self.vlc.get_next_queued_item()
                                if next_queued:
                                    # There's a queued item - check if the current track is NOT the queued item
                                    if current_item_id != next_queued['item_id']:
                                        if self._check_queue_auto_play_cooldown():
                                            logger.info(f"Track changed to {current_item_id} but we have queued item {next_queued['item_id']} - interrupting to play queued item")
                                            try:
                                                play_result = self.vlc.play_next_queued_item()
                                                logger.info(f"Auto-play result: {play_result}")
                                                
                                                if play_result.get("success"):
                                                    logger.info(f"Auto-played next queued item: {play_result.get('item_name', 'Unknown')}")
                                                    
                                                    # Optionally notify in Discord if notification channel is set
                                                    channel_id = getattr(Config, 'WATCH_ANNOUNCE_CHANNEL_ID', 0)
                                                    if channel_id:
                                                        try:
                                                            channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
                                                            if channel:
                                                                await channel.send(f"Auto-playing next queued item: **{play_result.get('item_name', 'Unknown')}**")
                                                        except Exception as e:
                                                            logger.error(f"Failed to send auto-play notification: {e}")
                                                        
                                                else:
                                                    logger.warning(f"Auto-play failed: {play_result.get('error', 'Unknown error')}")
                                            except Exception as e:
                                                logger.error(f"Error auto-playing next queued item: {e}")
                            
                            # Priority 2: For state changes to stopped OR paused, check if we should auto-play next queued item
                            # (movies often go to paused state when they end, not stopped)
                            elif state_changed and current_state in ['stopped', 'paused'] and self.vlc.get_next_queued_item():
                                if self._check_queue_auto_play_cooldown():
                                    try:
                                        logger.info(f"Track {current_state} - checking for next queued item to auto-play")
                                        next_queued = self.vlc.get_next_queued_item()
                                        logger.info(f"Next queued item found: {next_queued}")
                                        
                                        # Additional check: if paused, make sure we're actually at the end
                                        should_auto_play = True
                                        if current_state == 'paused':
                                            try:
                                                status = self.vlc.get_status()
                                                if status is not None:
                                                    time_elem = status.find('time')
                                                    length_elem = status.find('length')
                                                    if time_elem is not None and length_elem is not None:
                                                        current_time = int(time_elem.text)
                                                        total_length = int(length_elem.text)
                                                        # Only auto-play if we're within 3 seconds of the end
                                                        if total_length > 0 and (total_length - current_time) > 3:
                                                            should_auto_play = False
                                                            logger.debug(f"Paused but not at end: {current_time}/{total_length}s - not auto-playing")
                                            except Exception as e:
                                                logger.debug(f"Could not check time position: {e}")
                                        
                                        if should_auto_play:
                                            play_result = self.vlc.play_next_queued_item()
                                            logger.info(f"Auto-play result: {play_result}")
                                            
                                            if play_result.get("success"):
                                                logger.info(f"Auto-played next queued item: {play_result.get('item_name', 'Unknown')}")
                                                
                                                # Optionally notify in Discord if notification channel is set
                                                channel_id = getattr(Config, 'WATCH_ANNOUNCE_CHANNEL_ID', 0)
                                                if channel_id:
                                                    try:
                                                        channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
                                                        if channel:
                                                            await channel.send(f"Auto-playing next queued item: **{play_result.get('item_name', 'Unknown')}**")
                                                    except Exception as e:
                                                        logger.error(f"Failed to send auto-play notification: {e}")
                                                # Update presence to show the newly playing queued item
                                                try:
                                                    await self._set_presence(play_result.get('item_name'), reason="auto-queue (end detection)")
                                                except Exception:
                                                    pass
                                            else:
                                                logger.warning(f"Auto-play failed: {play_result.get('error', 'Unknown error')}")
                                            
                                    except Exception as e:
                                        logger.error(f"Error auto-playing next queued item: {e}")
                            
                            # Handle normal queue transitions for position changes (only if we didn't intercept)
                            if position_changed and current_item_id:
                                try:
                                    # Check for queue transitions and shuffle restoration
                                    queue_result = self.vlc.check_and_handle_queue_transition(current_item_id)
                                    
                                    # Log any queue transitions
                                    if queue_result.get("transitions"):
                                        for transition in queue_result["transitions"]:
                                            if transition["action"] == "shuffle_restored":
                                                logger.info(f"Queue system restored shuffle after item {transition['item_id']} finished")
                                                
                                                # Optionally notify in Discord if notification channel is set
                                                channel_id = getattr(Config, 'WATCH_ANNOUNCE_CHANNEL_ID', 0)
                                                if channel_id:
                                                    try:
                                                        channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
                                                        if channel:
                                                            await channel.send("Queue finished, shuffle mode restored.")
                                                    except Exception as e:
                                                        logger.error(f"Failed to send shuffle restored notification: {e}")
                                    
                                except Exception as e:
                                    logger.error(f"Error handling queue transition: {e}")
                            
                            # Detect when the last playing item finished (for shuffle restoration)
                            if position_changed and self.last_known_playing_item:
                                last_item_id = self.last_known_playing_item.get('id')
                                if last_item_id and last_item_id != current_item_id:
                                    # The last playing item is no longer playing - it finished
                                    try:
                                        self.vlc._handle_queued_item_finished(last_item_id)
                                        # Ensure playback rate is reset to normal after an item finishes
                                        try:
                                            self.vlc.set_rate(1.0)
                                            logger.debug("Playback rate reset to 1.0 after item finished")
                                        except Exception as e:
                                            logger.debug(f"Failed to reset playback rate after finish: {e}")
                                    except Exception as e:
                                        logger.error(f"Error handling finished item {last_item_id}: {e}")
                        
                        if state_changed or position_changed:
                            # Get item name if available
                            item_name = None
                            if current_item is not None:
                                item_name = current_item.get('name')
                                
                            # Log the change regardless of notification channel
                            if state_changed:
                                logger.info(f"VLC state changed to: {current_state}")
                            elif position_changed:
                                logger.info(f"Track changed to: {item_name or 'Unknown'} #{current_position if current_position else 'N/A'}")

                            # Update presence on normal track transitions (no queue intervention)
                            try:
                                if position_changed and item_name:
                                    await self._set_presence(item_name, reason="track change")
                            except Exception:
                                pass
                            
                            # Only send Discord message if a notification channel is configured
                            channel_ids = Config.get_announce_channel_ids()
                            now_ts = asyncio.get_event_loop().time()
                            # If the bot itself initiated the change, suppress one-time auto announce and clear the flag
                            if self._command_initiated_change:
                                self.logger.debug("Auto announce suppressed: command-initiated change")
                                self._command_initiated_change = False
                                continue
                            # Hard suppression: if we just sent a command-driven Now Playing, skip auto announce entirely (short window)
                            if position_changed and (now_ts - self._last_command_announce_ts) < self._auto_suppress_seconds:
                                self.logger.debug("Auto announce suppressed: recent command-driven Now Playing")
                            # Note: do not suppress by ID/name to allow manual selection announcements
                            elif channel_ids and (now_ts - self._last_command_announce_ts) > 1 and now_ts >= self._suppress_auto_announce_until:
                                # Use unified announcer
                                await self._announce_now_playing('monitor', current_item, current_position)
                            elif not channel_ids:
                                self.logger.debug("Track change announcement skipped: No announcement channels configured.")
                            else:
                                self.logger.debug("Track change announcement skipped: Debounced.")
                    
                    # Update last known state
                    self.last_known_state = current_state
                    self.last_known_position = current_position
                    self.last_known_playing_item = current_item  # Track the current playing item
                    
                    # Priority 3: End-of-track detection - check if current track is about to end
                    if current_state in ['playing', 'paused'] and self.vlc.get_next_queued_item():
                        # This check is to see if we are near the end of the media.
                        # If we are, we can be more aggressive about checking for the next item.
                        # This helps in cases where the state change to 'stopped' is delayed.
                        try:
                            status = self.vlc.get_status()
                            if status is not None:
                                time_elem = status.find('time')
                                length_elem = status.find('length')
                                if time_elem is not None and length_elem is not None:
                                    current_time = int(time_elem.text)
                                    total_time = int(length_elem.text)
                                    # If within 3 seconds of the end, we might want to act.
                                    if total_time > 0 and (total_time - current_time) < 3:
                                        if self._check_queue_auto_play_cooldown():
                                            logger.info("Track is near the end, preparing to auto-play next queued item.")
                                            # This path is tricky because we might preemptively switch.
                                            # For now, we just log. The main 'stopped'/'paused' handler will do the work.
                        except Exception as e:
                            logger.debug(f"Error in end-of-track detection: {e}")

                    # If VLC pauses at the very end of a track (common behavior) and there's no queued item,
                    # clear presence to avoid showing a stale title
                    try:
                        if current_state == 'paused' and not self.vlc.get_next_queued_item():
                            status = self.vlc.get_status()
                            if status is not None:
                                time_elem = status.find('time')
                                length_elem = status.find('length')
                                if time_elem is not None and length_elem is not None:
                                    current_time = int(time_elem.text)
                                    total_length = int(length_elem.text)
                                    if total_length > 0 and (total_length - current_time) <= 3:
                                        # Near end while paused and nothing queued -> clear presence
                                        await self._set_presence(None, reason="paused at end")
                                        logger.info("Cleared presence: VLC paused at track end and no queued items")
                    except Exception as e:
                        logger.debug(f"Paused-end presence clear check failed: {e}")
                    
                    # Enhanced periodic check: If we have queued items, ensure they get played
                    next_queued = self.vlc.get_next_queued_item()
                    if next_queued:
                        # Case 1: VLC is stopped and we have queued items
                        if current_state == 'stopped':
                            # Reset playback rate when VLC has stopped (file finished)
                            try:
                                self.vlc.set_rate(1.0)
                            except Exception:
                                pass
                            if self._check_queue_auto_play_cooldown():
                                try:
                                    play_result = self.vlc.play_next_queued_item()
                                    logger.info(f"Auto-play result from stopped state: {play_result}")
                                    if play_result.get("success"):
                                        logger.info(f"Auto-played next queued item: {play_result.get('item_name', 'Unknown')}")
                                        # Optionally notify
                                        channel_id = getattr(Config, 'WATCH_ANNOUNCE_CHANNEL_ID', 0)
                                        if channel_id:
                                            try:
                                                channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
                                                if channel:
                                                    await channel.send(f"Auto-playing next queued item: **{play_result.get('item_name', 'Unknown')}**")
                                            except Exception as e:
                                                logger.error(f"Failed to send auto-play notification: {e}")
                                        # Update presence
                                        try:
                                            await self._set_presence(play_result.get('item_name'), reason="auto-queue (stopped)")
                                        except Exception:
                                            pass
                                    else:
                                        logger.warning(f"Auto-play from stopped state failed: {play_result.get('error', 'Unknown error')}")
                                except Exception as e:
                                    logger.error(f"Error auto-playing from stopped state: {e}")
                        
                        # Case 2: VLC is playing but wrong item (queue was bypassed)
                        elif current_state == 'playing' and current_item:
                            current_item_id = current_item.get('id')
                            if current_item_id != next_queued['item_id']:
                                if self._check_queue_auto_play_cooldown():
                                    try:
                                        logger.info(f"Periodic check: Wrong item playing ({current_item_id}), should be queued item ({next_queued['item_id']}) - correcting")
                                        play_result = self.vlc.play_next_queued_item()
                                        
                                        if play_result.get("success"):
                                            logger.info(f"Periodic correction successful: {play_result.get('item_name', 'Unknown')}")
                                            try:
                                                await self._set_presence(play_result.get('item_name'), reason="periodic correction (wrong item)")
                                            except Exception:
                                                pass
                                    except Exception as e:
                                        logger.error(f"Error in periodic queue correction: {e}")
                    
            except Exception as e:
                logger.error(f"Error in VLC monitoring task: {e}")
            
            # Wait before next check
            await asyncio.sleep(0.5)  # Check every half second for more responsive queue handling

    @commands.command(name='speedstatus', aliases=['spdstatus', 'sr'])
    @commands.has_any_role(*Config.ALLOWED_ROLES)
    async def speed_status(self, ctx):
        """Report current VLC playback rate/speed.

        Usage examples are shown with the configured prefix in `!!controls`.
        """
        try:
            status = self.vlc.get_status()
            if not status:
                await ctx.send('Error: Could not access VLC status')
                return

            rate_elem = status.find('rate')
            rate_val = None
            if rate_elem is not None and rate_elem.text:
                try:
                    rate_val = float(rate_elem.text)
                except Exception:
                    # Try to clean whitespace/formatting
                    try:
                        rate_val = float(rate_elem.text.strip())
                    except Exception:
                        rate_val = None

            if rate_val is not None:
                embed = discord.Embed(
                    title="Playback Speed",
                    description=f"Current playback rate: {rate_val:.2f}x",
                    color=discord.Color.blue()
                )
            else:
                embed = discord.Embed(
                    title="Playback Speed",
                    description="Current playback rate: unknown (VLC did not expose rate in status)",
                    color=discord.Color.orange()
                )

            await ctx.send(embed=embed)
        except Exception as e:
            logger.error(f"Failed to get playback speed: {e}")
            await ctx.send(f"Error reading playback speed: {e}")

    @commands.command(name='sub_next', aliases=['subn','sub+','subnext'])
    @commands.has_any_role(*Config.ALLOWED_ROLES)
    async def subtitle_next(self, ctx):
        """Cycle to the next subtitle track in VLC (if supported)."""
        try:
            ok = self.vlc.subtitle_next()
            if ok:
                embed = discord.Embed(
                    title="💬 Subtitles",
                    description="Switched to the next subtitle track.",
                    color=discord.Color.green()
                )
                await ctx.send(embed=embed)
            else:
                embed = discord.Embed(
                    title="💬 Subtitles",
                    description=(
                        "Could not cycle subtitle track. Ensure VLC's HTTP interface supports "
                        "relative subtitle changes (subtitle_track +1)."
                    ),
                    color=discord.Color.orange()
                )
                await ctx.send(embed=embed)
        except Exception as e:
            logger.error(f"subtitle_next error: {e}")
            await ctx.send(f"Error cycling subtitles: {e}")

    @commands.command(name='sub_prev', aliases=['subp','sub-','subprev'])
    @commands.has_any_role(*Config.ALLOWED_ROLES)
    async def subtitle_prev(self, ctx):
        """Cycle to the previous subtitle track in VLC (if supported)."""
        try:
            ok = self.vlc.subtitle_prev()
            if ok:
                embed = discord.Embed(
                    title="💬 Subtitles",
                    description="Switched to the previous subtitle track.",
                    color=discord.Color.green()
                )
                await ctx.send(embed=embed)
            else:
                embed = discord.Embed(
                    title="💬 Subtitles",
                    description=(
                        "Could not cycle subtitle track. Ensure VLC's HTTP interface supports "
                        "relative subtitle changes (subtitle_track -1)."
                    ),
                    color=discord.Color.orange()
                )
                await ctx.send(embed=embed)
        except Exception as e:
            logger.error(f"subtitle_prev error: {e}")
            await ctx.send(f"Error cycling subtitles: {e}")

    @commands.command(name='sub_list', aliases=['subs','slist'])
    @commands.has_any_role(*Config.ALLOWED_ROLES)
    async def subtitle_list(self, ctx):
        """List available subtitle tracks and indicate which is selected."""
        try:
            try:
                await ctx.trigger_typing()
            except Exception:
                pass
            
            tracks = self.vlc.get_subtitle_tracks()
            if tracks is None:
                await ctx.send("Couldn't retrieve subtitle tracks from VLC.")
                return
            if len(tracks) == 0:
                embed = discord.Embed(
                    title="💬 Subtitles",
                    description="No subtitle tracks reported by VLC for the current media.",
                    color=discord.Color.orange()
                )
                await ctx.send(embed=embed)
                return
            
            # Mark selected track based on our tracking (VLC API doesn't expose this)
            if self.selected_subtitle_stream_index is not None:
                for tr in tracks:
                    if tr.get('stream_index') == self.selected_subtitle_stream_index:
                        tr['selected'] = True
                        logger.info(f"Marked track as selected: stream_index={self.selected_subtitle_stream_index}")
                        break
            
            # Build a neatly aligned list
            lines = []
            # Determine width for index alignment
            max_index = max((tr.get('index') or i) for i, tr in enumerate(tracks, start=1))
            for i, tr in enumerate(tracks, start=1):
                # Use checkmark/empty circle markers for alignment
                mark = "✅" if tr.get('selected') else "⚪"
                ui_idx = tr.get('index') or i
                name = tr.get('name') or f"Track {ui_idx}"
                lines.append(f"{mark} **{ui_idx}.** {name}")
            list_text = "\n".join(lines[:20]) + ("\n..." if len(lines) > 20 else "")
            embed = discord.Embed(
                title="💬 Subtitle Tracks",
                description=list_text,
                color=discord.Color.blue()
            )
            # Show current selection explicitly
            try:
                selected_track = next((tr for tr in tracks if tr.get('selected')), None)
            except Exception:
                selected_track = None
            if selected_track:
                cur_idx = selected_track.get('index') or selected_track.get('id')
                cur_name = selected_track.get('name') or (f"Track {cur_idx}" if cur_idx is not None else "Track")
                embed.add_field(name="Current", value=f"✅ {cur_name} ({cur_idx})", inline=True)
            else:
                embed.add_field(name="Current", value="⚪ Off", inline=True)
            embed.add_field(
                name="Usage",
                value=(
                    f"Use {format_cmd_inline('sub_set <number>')} to select by position (as shown), or "
                    f"{format_cmd_inline('sub_set off')} to disable."
                ),
                inline=False
            )
            await ctx.send(embed=embed)
        except Exception as e:
            logger.error(f"subtitle_list error: {e}")
            await ctx.send(f"Error listing subtitles: {e}")

    @commands.command(name='sub_set', aliases=['subset','subid'])
    @commands.has_any_role(*Config.ALLOWED_ROLES)
    async def subtitle_set(self, ctx, track_id: str):
        """Select a specific subtitle track by position (from sub_list), or 'off' to disable.

        Examples:
        - sub_set 2          # Select 2nd subtitle in the list
        - sub_set 1          # Select 1st subtitle in the list
        - sub_set off        # Disable subtitles
        """
        try:
            if not track_id:
                await ctx.send(f"Usage: {format_cmd_inline('sub_set <number|off>')}")
                return
            # Fetch tracks to support index-based addressing
            tracks = self.vlc.get_subtitle_tracks() or []
            logger.info(f"sub_set: User requested '{track_id}', found {len(tracks)} tracks")
            
            # Log all tracks for debugging
            for i, tr in enumerate(tracks, start=1):
                logger.debug(f"  Track {i}: id={tr.get('id')}, index={tr.get('index')}, stream_index={tr.get('stream_index')}, name={tr.get('name')}, selected={tr.get('selected')}")

            # Handle disable synonyms
            tokens_off = {"off", "none", "disable", "disabled"}
            if track_id.lower() in tokens_off:
                logger.info(f"sub_set: Disabling subtitles")
                # Try -1 first, fallback to 0 for older VLC versions
                ok = self.vlc.set_subtitle_track(-1)
                if not ok:
                    ok = self.vlc.set_subtitle_track(0)
                if not ok:
                    await ctx.send("Failed to disable subtitles (tried -1 and 0).")
                    return
                # Track that subtitles are disabled
                self.selected_subtitle_stream_index = None
                embed = discord.Embed(
                    title="💬 Subtitles",
                    description="Subtitles disabled.",
                    color=discord.Color.green()
                )
                await ctx.send(embed=embed)
                return

            # Parse position as 1-based GUI order
            try:
                pos_index = int(track_id)
            except Exception:
                await ctx.send(f"Please provide a numeric position or 'off'. Use {format_cmd_inline('sub_list')} to see available tracks.")
                return
            
            if pos_index < 1 or pos_index > len(tracks):
                await ctx.send(f"Position out of range. There are {len(tracks)} subtitle tracks. Use {format_cmd_inline('sub_list')} to see them.")
                return
            
            logger.info(f"sub_set: Looking for track at position {pos_index}")
            
            # Find track by GUI order (index field)
            tid = None
            stream_idx = None
            selected_track = None
            for tr in tracks:
                if tr.get('index') == pos_index:
                    tid = tr.get('id')
                    stream_idx = tr.get('stream_index')
                    selected_track = tr
                    logger.info(f"sub_set: Found track by index: id={tid}, stream_index={stream_idx}, name={tr.get('name')}")
                    break
            
            # Fallback to list order if no index mapping available
            if tid is None:
                selected_track = tracks[pos_index - 1]
                tid = selected_track.get('id')
                stream_idx = selected_track.get('stream_index')
                logger.info(f"sub_set: Using list order fallback: id={tid}, stream_index={stream_idx}, name={selected_track.get('name')}")
            
            # Try setting subtitle track - prefer stream_index over track ID
            # VLC HTTP API typically uses stream index (0, 1, 2...) not track IDs
            ok = False
            if stream_idx is not None:
                logger.info(f"sub_set: Attempting to set subtitle by stream_index={stream_idx}")
                ok = self.vlc.set_subtitle_track(stream_idx)
                if ok:
                    logger.info(f"sub_set: Successfully set by stream_index={stream_idx}")
                    # Track the selected subtitle
                    self.selected_subtitle_stream_index = stream_idx
            
            if not ok and tid is not None:
                logger.info(f"sub_set: Attempting to set subtitle by track id={tid}")
                ok = self.vlc.set_subtitle_track(tid)
                if ok:
                    logger.info(f"sub_set: Successfully set by track id={tid}")
                    self.selected_subtitle_stream_index = stream_idx if stream_idx else tid
            
            if not ok:
                # Try direct position fallback for VLC versions that support it
                logger.warning(f"sub_set: Failed to set by stream_index and id, trying position-based fallback")
                ok = self.vlc.set_subtitle_track(pos_index - 1)
                if ok:
                    logger.info(f"sub_set: Successfully set by position {pos_index - 1}")
                    self.selected_subtitle_stream_index = stream_idx if stream_idx else (pos_index - 1)
                else:
                    ok = self.vlc.set_subtitle_track(pos_index)
                    if ok:
                        logger.info(f"sub_set: Successfully set by position {pos_index}")
                        self.selected_subtitle_stream_index = stream_idx if stream_idx else pos_index
                
            if not ok:
                embed = discord.Embed(
                    title="💬 Subtitles",
                    description=f"Failed to set subtitle track {pos_index}. Use {format_cmd_inline('sub_list')} to verify available tracks.",
                    color=discord.Color.orange()
                )
                await ctx.send(embed=embed)
                return

            # Confirm new selection by re-reading tracks
            tracks2 = self.vlc.get_subtitle_tracks() or []
            selected_name = None
            selected_pos = None
            # Use the track we attempted to set as a fallback if VLC doesn't mark selection
            fallback_name = selected_track.get('name') if selected_track else None
            fallback_pos = selected_track.get('index') if selected_track else pos_index
            for tr in tracks2:
                if tr.get('selected'):
                    selected_name = tr.get('name')
                    selected_pos = tr.get('index')
                    logger.info(f"sub_set: Confirmed selection - pos={selected_pos}, name={selected_name}")
                    break

            if selected_pos is None:
                # Show the intended track as a helpful hint even if VLC didn't mark it selected yet
                desc = f"Set subtitle to {fallback_pos}: {fallback_name or 'Unknown'}"
            else:
                desc = f"Selected subtitle {selected_pos}: {selected_name or 'Unknown'}"

            embed = discord.Embed(
                title="💬 Subtitles",
                description=desc,
                color=discord.Color.green()
            )
            if tracks2:
                embed.add_field(
                    name="Tip",
                    value=f"Use {format_cmd_inline('sub_set off')} to disable subtitles.",
                    inline=False
                )
            await ctx.send(embed=embed)
        except Exception as e:
            logger.error(f"subtitle_set error: {e}")
            await ctx.send(f"Error setting subtitles: {e}")

    @commands.command(name='status', aliases=['np', 'nowplaying'])
    @commands.has_any_role(*Config.ALLOWED_ROLES)
    async def status(self, ctx):
        """Show current VLC status and what's playing"""
        embed = await self.get_status_embed()
        if embed:
            await ctx.send(embed=embed)
        else:
            await ctx.send("Could not retrieve VLC status.")

    async def get_status_embed(self):
        """Generate a rich embed for the current VLC status."""
        try:
            status = self.vlc.get_status()
            if not status:
                return None

            state = status.find('state').text
            
            playlist = self.vlc.get_playlist()
            position, current_item = self._find_current_position(playlist)
            
            item_name = None
            if current_item is not None:
                item_name = current_item.get('name')

            # Try to get TMDB metadata
            tmdb_embed = None
            if item_name:
                clean_title, year = MediaUtils.parse_movie_filename(item_name)
                tmdb_embed = self.tmdb.get_movie_metadata(clean_title, year)
                if not tmdb_embed:
                    tmdb_embed = self.tmdb.get_tv_metadata(clean_title)

            if tmdb_embed:
                # Use the rich embed from TMDB
                final_embed = tmdb_embed
                final_embed.title = f"Now Playing: {final_embed.title}"
            else:
                # Create a basic embed
                title = "VLC Status"
                if item_name:
                    title = f"Now Playing: {item_name}"
                
                final_embed = discord.Embed(title=title, color=discord.Color.blue())

            # Add playback state and position
            state_emoji_map = {
                'playing': '▶️',
                'paused': '⏸️',
                'stopped': '⏹️'
            }
            state_text = f"{state_emoji_map.get(state, '')} {state.capitalize()}".strip()
            
            if position and item_name:
                final_embed.add_field(name="Playlist", value=f"#{position}", inline=True)

            final_embed.add_field(name="State", value=state_text, inline=True)

            # Add time/duration
            time_elem = status.find('time')
            length_elem = status.find('length')
            if time_elem is not None and length_elem is not None:
                try:
                    current_time = int(time_elem.text)
                    total_time = int(length_elem.text)
                    if total_time > 0:
                        progress = f"{MediaUtils.format_time(current_time)} / {MediaUtils.format_time(total_time)}"
                        final_embed.add_field(name="Progress", value=progress, inline=False)
                except (ValueError, TypeError):
                    pass # Ignore if time/length are not valid numbers

            # Add footer
            try:
                if Config.KOFI_URL:
                    final_embed.add_field(name="Support kutsaratinidor by supporting CtrlVee", value=f"☕ <{Config.KOFI_URL}>", inline=False)
            except Exception:
                pass

            if not final_embed.thumbnail and hasattr(self.bot.user, 'display_avatar'):
                final_embed.set_thumbnail(url=self.bot.user.display_avatar.url)

            return final_embed

        except Exception as e:
            self.logger.error(f"Error getting status embed: {e}")
            return None
            
    @commands.command(name='play')
    @commands.has_any_role(*Config.ALLOWED_ROLES)
    async def play(self, ctx):
        """Start or resume playback"""
        if not await self._check_cooldown(ctx):
            return

        status = self.vlc.get_status()
        if not status:
            await ctx.send('Error: Could not access VLC')
            return

        state = status.find('state').text
        if state == 'playing':
            return

        if self.vlc.play():
            await asyncio.sleep(0.5)
            new_status = self.vlc.get_status()
            if new_status and new_status.find('state').text == 'playing':
                guild_id = str(ctx.guild.id) if ctx.guild else 'dm'
                self.last_state_change[guild_id] = asyncio.get_event_loop().time()
                logger.info("Playback started/resumed")
                embed = discord.Embed(
                    title="▶️ Playback started",
                    description="Playback started/resumed",
                    color=discord.Color.green()
                )
                await ctx.send(embed=embed)

    @commands.command(name='pause')
    @commands.has_any_role(*Config.ALLOWED_ROLES)
    async def pause(self, ctx):
        """Pause playback"""
        if not await self._check_cooldown(ctx):
            return

        status = self.vlc.get_status()
        if not status:
            await ctx.send('Error: Could not access VLC')
            return

        state = status.find('state').text
        if state != 'playing':
            return

        if self.vlc.pause():
            await asyncio.sleep(0.5)
            new_status = self.vlc.get_status()
            if new_status and new_status.find('state').text == 'paused':
                guild_id = str(ctx.guild.id) if ctx.guild else 'dm'
                self.last_state_change[guild_id] = asyncio.get_event_loop().time()
                logger.info("Playback paused")
                await ctx.send('Playback paused')

    @commands.command(name='stop')
    @commands.has_any_role(*Config.ALLOWED_ROLES)
    async def stop(self, ctx):
        """Stop playback"""
        if self.vlc.stop():
            logger.info("Playback stopped")
            embed = discord.Embed(
                title="⏹️ Playback stopped",
                description="Playback has been stopped",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
        else:
            logger.error("Failed to stop playback")
            embed = discord.Embed(
                title="⏹️ Playback stop failed",
                description="Error: Could not stop playback",
                color=discord.Color.dark_red()
            )
            await ctx.send(embed=embed)

    @commands.command(name='restart')
    @commands.has_any_role(*Config.ALLOWED_ROLES)
    async def restart(self, ctx):
        """Restart current file from the beginning"""
        if self.vlc.seek("0"):
            logger.info("Restarted current file from beginning")
            await ctx.send('Restarted current file from the beginning')
        else:
            logger.error("Failed to restart file")
            await ctx.send('Error: Could not restart file')

    @commands.command(name='rewind', aliases=['rw'])
    @commands.has_any_role(*Config.ALLOWED_ROLES)
    async def rewind(self, ctx, seconds: int = 10):
        """Rewind playback by specified number of seconds"""
        if seconds <= 0:
            embed = discord.Embed(
                title="⏪ Rewind failed",
                description="Please specify a positive number of seconds",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            return

        if self.vlc.seek(f"-{seconds}"):
            embed = discord.Embed(
                title="⏪ Rewound",
                description=f"Rewound {seconds} seconds",
                color=discord.Color.green()
            )
            await ctx.send(embed=embed)
        else:
            embed = discord.Embed(
                title="⏪ Rewind failed",
                description="Error: Could not rewind",
                color=discord.Color.dark_red()
            )
            await ctx.send(embed=embed)
 
    @commands.command(name='forward', aliases=['ff'])
    @commands.has_any_role(*Config.ALLOWED_ROLES)
    async def forward(self, ctx, seconds: int = 10):
        """Fast forward playback by specified number of seconds"""
        if seconds <= 0:
            embed = discord.Embed(
                title="⏩ Fast-forward failed",
                description="Please specify a positive number of seconds",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            return

        if self.vlc.seek(f"+{seconds}"):
            embed = discord.Embed(
                title="⏩ Fast-forwarded",
                description=f"Fast forwarded {seconds} seconds",
                color=discord.Color.green()
            )
            await ctx.send(embed=embed)
        else:
            embed = discord.Embed(
                title="⏩ Fast-forward failed",
                description="Error: Could not fast forward",
                color=discord.Color.dark_red()
            )
            await ctx.send(embed=embed)
    
    @commands.command(name='play_num')
    @commands.has_any_role(*Config.ALLOWED_ROLES)
    async def play_number(self, ctx, number: int):
        """Play a specific item from the playlist by its number"""
        try:
            if number < 1:
                await ctx.send('Please provide a number greater than 0')
                return

            playlist = self.vlc.get_playlist()
            if not playlist:
                await ctx.send('Could not access VLC playlist')
                return

            items = playlist.findall('.//leaf')
            if not items:
                await ctx.send('Playlist is empty')
                return

            if number > len(items):
                await ctx.send(f'Number too high. Playlist has {len(items)} items')
                return

            item = items[number - 1]
            item_id = item.get('id')

            if self.vlc.play_item(item_id):
                logger.info(f"Loading playlist item #{number}")
                await ctx.send(f'Loading item #{number}...')
                await asyncio.sleep(3)  # Give VLC time to load and start playing the file
                
                status = self.vlc.get_status()
                if not status:
                    await ctx.send(f'Started playing item #{number}')
                    return
                    
                state = status.find('state').text
                if state != 'playing':
                    # If not playing yet, try to start playback
                    self.vlc.play()
                    await asyncio.sleep(2)
                    status = self.vlc.get_status()
                    if status:
                        state = status.find('state').text
                        if state != 'playing':
                            # One more try
                            self.vlc.play()
                            await asyncio.sleep(1)
                
                # Announce now playing via unified announcer
                await self._announce_now_playing('command', item, number)
                
                # Verify it's actually playing
                status = self.vlc.get_status()
                if status and status.find('state').text != 'playing':
                    await ctx.send(f"Warning: VLC might not be playing. Try using {format_cmd_inline('play')} if playback doesn't start.")
            else:
                await ctx.send('Error: Could not start playback')
        except ValueError:
            await ctx.send('Please provide a valid number')
            
    @commands.command(name='next')
    @commands.has_any_role(*Config.ALLOWED_ROLES)
    async def next_track(self, ctx):
        """Play next track in playlist (prioritizes queued items)"""
        if not await self._check_cooldown(ctx):
            return
        
        # First check if there are any queued items to play
        next_queued = self.vlc.get_next_queued_item()
        if next_queued:
            logger.info(f"Playing next queued item: {next_queued['item_name']}")
            result = self.vlc.play_next_queued_item()
            
            if result.get("success"):
                embed = discord.Embed(
                    title="🎵 Playing Queued Item",
                    description=f"Now playing: **{result['item_name']}**",
                    color=discord.Color.green()
                )
                embed.add_field(
                    name="Queue Info",
                    value=f"This was queued item #{result.get('queue_order', 'unknown')}",
                    inline=True
                )
                await ctx.send(embed=embed)
                # Update bot presence to the queued item's name (if enabled)
                try:
                    await self._set_presence(result.get('item_name'), reason="next (queued)")
                except Exception:
                    pass
                return
            else:
                await ctx.send(f"Error playing queued item: {result.get('error', 'Unknown error')}")
                # Fall through to normal next behavior
        
        # If no queued items or queue failed, use normal next behavior
        if self.vlc.next():
            logger.info("Loading next track")
            await ctx.send('Loading next track...')
            try:
                # Mark that this change was initiated by our command to suppress one auto announce
                self._command_initiated_change = True
            except Exception:
                pass
            await asyncio.sleep(3)  # Give VLC time to load and start playing the file
            
            status = self.vlc.get_status()
            if not status:
                await ctx.send('Skipped to next track')
                return
                
            state = status.find('state').text
            if state != 'playing':
                # If not playing yet, wait a bit longer
                await asyncio.sleep(2)
                status = self.vlc.get_status()
            
            playlist = self.vlc.get_playlist()
            if status and playlist:
                position, current_item = self._find_current_position(playlist)
                if current_item is not None:
                    await self._announce_now_playing('command', current_item, position)
                    try:
                        self._suppress_auto_announce_until = asyncio.get_event_loop().time() + 5.0
                    except Exception:
                        pass
                else:
                    await ctx.send('Skipped to next track')
            else:
                await ctx.send('Skipped to next track')
        else:
            await ctx.send('Error: Could not skip to next track')
            
    @commands.command(name='previous')
    @commands.has_any_role(*Config.ALLOWED_ROLES)
    async def previous_track(self, ctx):
        """Play previous track in playlist"""
        if not await self._check_cooldown(ctx):
            return
            
        if self.vlc.previous():
            logger.info("Loading previous track")
            await ctx.send('Loading previous track...')
            try:
                self._command_initiated_change = True
            except Exception:
                pass
            await asyncio.sleep(3)  # Give VLC time to load and start playing the file
            
            status = self.vlc.get_status()
            if not status:
                await ctx.send('Jumped to previous track')
                return
                
            state = status.find('state').text
            if state != 'playing':
                # If not playing yet, wait a bit longer
                await asyncio.sleep(2)
                status = self.vlc.get_status()
            
            playlist = self.vlc.get_playlist()
            if status and playlist:
                # Find current item
                current_item = None
                for item in playlist.findall('.//leaf'):
                    if item.get('current'):
                        current_item = item
                        break
                        
                if current_item is not None:
                    # Find the position number of the current item
                    position = None
                    for i, item in enumerate(playlist.findall('.//leaf')):
                        if item.get('id') == current_item.get('id'):
                            position = i + 1  # Convert to 1-based index
                            break
                    await self._announce_now_playing('command', current_item, position)
                    try:
                        self._suppress_auto_announce_until = asyncio.get_event_loop().time() + 5.0
                    except Exception:
                        pass
                else:
                    await ctx.send('Jumped to previous track')
            else:
                await ctx.send('Jumped to previous track')
        else:
            await ctx.send('Error: Could not jump to previous track')

            
    async def _check_vlc_connection(self, ctx):
        """Check if VLC is accessible and send error message if not
        
        Returns:
            bool: True if VLC is accessible, False if not
        """
        try:
            status = self.vlc.get_status()
            if not status:
                logger.error("Could not access VLC - HTTP interface may not be enabled")
                await ctx.send('Error: Could not access VLC. Make sure VLC is running with HTTP interface enabled.')
                return False
            logger.debug("VLC connection check successful")
            return True
        except Exception as e:
            logger.error(f"Error connecting to VLC: {str(e)}")
            await ctx.send(f'Error connecting to VLC: {str(e)}')
            return False
        
    async def _set_presence(self, name: str | None, reason: str | None = None):
        """Set the bot's Discord presence (throttled).

        Args:
            name: The activity name to show (e.g., movie title). If None, clears the activity.
        """
        # Respect global config toggle
        try:
            if not getattr(Config, 'ENABLE_PRESENCE', True):
                logger.debug("Presence updates disabled by config; skipping change")
                return
            now = asyncio.get_event_loop().time()
            # Always allow clearing presence. For setting a title, only throttle if
            # it's the same as the last title within the throttle window.
            if name is not None and (now - self._presence_last_set) < self._presence_throttle_seconds:
                if self._presence_last_name == name:
                    logger.debug(
                        f"Presence skipped due to throttle (same title within {self._presence_throttle_seconds}s): {name}"
                    )
                    return

            # Build an activity. Use 'Watching' to avoid Streaming URL requirements.
            if name:
                display_name = f"🎬 {name}"
                try:
                    activity = discord.Activity(type=discord.ActivityType.watching, name=display_name)
                except Exception:
                    # Fallback: still attempt to set as watching if streaming creation fails
                    activity = discord.Activity(type=discord.ActivityType.watching, name=display_name)
            else:
                activity = None

            # Attempt to change presence; non-fatal
            try:
                await self.bot.change_presence(activity=activity)
                self._presence_last_set = now
                prev = self._presence_last_name
                self._presence_last_name = name
                # Log updates sparingly: only log a clear once per stopped state
                if name:
                    logger.info(
                        f"Presence updated to: {display_name}" + (f" (reason: {reason})" if reason else "")
                    )
                else:
                    # Avoid spamming 'Presence cleared' when repeatedly stopped
                    try:
                        if getattr(self, '_presence_cleared_once', False):
                            # Already logged this state; keep quiet
                            pass
                        else:
                            logger.info("Presence cleared" + (f" (reason: {reason})" if reason else ""))
                            self._presence_cleared_once = True
                    except Exception:
                        logger.info("Presence cleared" + (f" (reason: {reason})" if reason else ""))
            except Exception as e:
                logger.debug(f"Failed to set presence: {e}")
        except Exception as e:
            logger.debug(f"Presence update error: {e}")
            
    @commands.command(name='status')
    async def status(self, ctx):
        """Show current VLC status with enhanced metadata"""
        if not await self._check_vlc_connection(ctx):
            return
        # Use formatting helper for commands
            
        status = self.vlc.get_status()
            
        state = status.find('state').text
        current = status.find('information')
        
        logger.info(f"Current VLC state: {state}")
        logger.debug(f"Status - Current Info: {ET.tostring(current).decode() if current is not None else 'None'}")
        
        # Get position by finding current item in playlist
        playlist = self.vlc.get_playlist()
        current_position = None
        if playlist is not None:
            # Find current item and its position
            for i, item in enumerate(playlist.findall('.//leaf')):
                if item.get('current'):
                    current_position = i + 1  # Convert to 1-based index
                    break
            logger.info(f"Current position in playlist: {current_position if current_position else 'not found'}")
        
        # Compute media library size
        size_bytes = self.watch_service.get_total_media_size() if self.watch_service else 0
        def human_size(num):
            for unit in ['B','KB','MB','GB','TB']:
                if num < 1024.0:
                    return f"{num:.2f} {unit}"
                num /= 1024.0
            return f"{num:.2f} PB"

        embed = discord.Embed(
            title="VLC Status",
            color=discord.Color.blue()
        )
        embed.add_field(name="State", value=state.capitalize(), inline=True)
        embed.add_field(name="Media Library Size", value=human_size(size_bytes), inline=True)
        
        if state != 'stopped':
            # Get the name from information/category/info[@name='filename']
            name = None
            if current is not None:
                for category in current.findall('category'):
                    for info in category.findall('info'):
                        if info.get('name') == 'filename':
                            name = info.text
                            break
                    if name:
                        break
            
            # If we couldn't get name from status, try playlist
            if not name:
                playlist = self.vlc.get_playlist()
                if playlist is not None:
                    for item in playlist.findall('.//leaf'):
                        if item.get('current'):
                            name = item.get('name')
                            break
            
            if name:
                logger.debug(f"Status - Found name: {name}")
                # Get movie metadata
                search_title, search_year = MediaUtils.parse_movie_filename(name)
                logger.debug(f"Status - Cleaned title: {search_title}, Year: {search_year}")
                movie_data = self.tmdb.get_movie_metadata(search_title, search_year)
                logger.debug(f"Status - Movie data: {movie_data}")
                
                # Log movie data attributes
                if movie_data:
                    logger.debug("Status - Movie data attributes:")
                    logger.debug(f"  - Has title: {hasattr(movie_data, 'title')}")
                    logger.debug(f"  - Has overview: {hasattr(movie_data, 'overview')}")
                    logger.debug(f"  - Has tmdb_url: {hasattr(movie_data, 'tmdb_url')}")
                    logger.debug(f"  - Has fields: {hasattr(movie_data, 'fields')}")
                    if hasattr(movie_data, 'fields'):
                        logger.debug("  - Fields:")
                        for field in movie_data.fields:
                            logger.debug(f"    - {field.name}: {field.value}")
                
                # Add position info if available
                position_text = f" (Item {current_position})" if current_position is not None else ""
                
                if movie_data:
                    logger.debug("Status - Using TMDB movie data")
                    # Use the movie_data embed directly and update the state field
                    embed = movie_data
                    embed.insert_field_at(0, name="State", value=state.capitalize(), inline=True)
                else:
                    logger.debug("Status - No movie data, using filename")
                    # Add the filename to our existing embed
                    embed.add_field(
                        name="Now Playing",
                        value=name,
                        inline=False
                    )
                    
                # Add progress information
                time_elem = status.find('time')
                length_elem = status.find('length')
                if time_elem is not None and length_elem is not None:
                    time = int(time_elem.text)
                    length = int(length_elem.text)
                    progress = f"{time//60}:{time%60:02d}/{length//60}:{length%60:02d}"
                    embed.add_field(name="Progress", value=progress, inline=True)
                    
                # Add position note as a field with bold formatting after progress
                if current_position is not None:
                    embed.add_field(name="Quick Replay", value=f"💡 Use {format_cmd_inline(f'play_num {current_position}')} to play this item again", inline=False)
                
                # Ensure Support field (Ko-fi) is present and a thumbnail is set for visibility
                try:
                    if Config.KOFI_URL:
                        try:
                            embed.add_field(name="Support kutsaratinidor by supporting CtrlVee", value=f"☕ {f'<{Config.KOFI_URL}>'}", inline=False)
                        except Exception:
                            pass
                except Exception:
                    pass

                # If embed has no thumbnail, attempt to set bot avatar as thumbnail to increase visibility
                try:
                    if not embed.thumbnail or not getattr(embed.thumbnail, 'url', None):
                        bot_user = getattr(self.bot, 'user', None)
                        if bot_user and getattr(bot_user, 'display_avatar', None):
                            embed.set_thumbnail(url=bot_user.display_avatar.url)
                except Exception:
                    pass
            else:
                # Playing but no name found
                embed.add_field(name="Now Playing", value="Unknown item", inline=False)
        else:
            # VLC is stopped - add helpful information
            embed.add_field(name="Now Playing", value="Nothing currently playing", inline=False)
            
            # Add playlist information if available
            if playlist is not None:
                items = playlist.findall('.//leaf')
                playlist_count = len(items)
                if playlist_count > 0:
                    embed.add_field(
                        name="Playlist Info", 
                        value=f"{playlist_count} items in playlist\nUse {format_cmd_inline('play')} to resume or {format_cmd_inline('play_num <number>')} to play a specific item",
                        inline=False
                    )
                else:
                    embed.add_field(name="Playlist Info", value="Playlist is empty", inline=False)
        
        await ctx.send(embed=embed)

    @commands.command(name='queue_next', aliases=['qnext'])
    @commands.has_any_role(*Config.ALLOWED_ROLES)
    async def queue_next(self, ctx, number: int):
        """Queue a specific playlist item to play next using soft queue system (handles shuffle intelligently)"""
        try:
            if number < 1:
                await ctx.send('Please provide a number greater than 0')
                return

            playlist = self.vlc.get_playlist()
            if not playlist:
                await ctx.send('Could not access VLC playlist')
                return

            items = playlist.findall('.//leaf')
            if not items:
                await ctx.send('Playlist is empty')
                return

            if number > len(items):
                await ctx.send(f'Number too high. Playlist has {len(items)} items')
                return

            item = items[number - 1]
            item_id = item.get('id')
            item_name = item.get('name', 'Unknown')

            # Queue the item
            result = self.vlc.queue_item_next(item_id)
            
            if result.get("success"):
                embed = discord.Embed(
                    title="🎵 Item Queued",
                    color=discord.Color.green()
                )
                embed.add_field(
                    name="Queued Item",
                    value=f"**{item_name}**\n📋 Playlist position: #{number}",
                    inline=False
                )
                embed.add_field(
                    name="Queue Position",
                    value=f"#{result['queue_order']} of {result.get('total_queued', 1)} in queue",
                    inline=True
                )
                
                await ctx.send(embed=embed)
                logger.info(f"Queued item #{number} ({item_name}) to play next")
            else:
                await ctx.send(f'Error queuing item: {result.get("error", "Unknown error")}')
                
        except ValueError:
            await ctx.send('Please provide a valid number')
        except Exception as e:
            logger.error(f"Error in queue_next command: {e}")
            await ctx.send(f'Error queuing item: {str(e)}')

    @commands.command(name='queue_status', aliases=['qstatus'])
    @commands.has_any_role(*Config.ALLOWED_ROLES)
    async def queue_status(self, ctx):
        """Show current soft queue status and shuffle state"""
        try:
            queue_status = self.vlc.get_queue_status()
            shuffle_on = queue_status.get("shuffle_currently_on", False)
            
            embed = discord.Embed(
                title="📋 Queue Status",
                color=discord.Color.blue()
            )
            
            # Get queue count for title
            queued_items = queue_status.get("queued_items", {})
            queue_count = len(queued_items)
            if queue_count > 0:
                embed.title = f"📋 Queue Status ({queue_count} item{'s' if queue_count != 1 else ''})"
            
            # Active queued items
            if queued_items:
                # Get playlist to map item IDs to titles and positions
                playlist = self.vlc.get_playlist()
                playlist_map = {}
                if playlist:
                    for idx, item in enumerate(playlist.findall('.//leaf'), 1):
                        item_id = item.get('id')
                        item_name = item.get('name', 'Unknown')
                        if item_id:
                            playlist_map[item_id] = {
                                'name': item_name,
                                'position': idx
                            }
                
                queue_list = []
                for item_id, info in queued_items.items():
                    # Get item details from playlist
                    if item_id in playlist_map:
                        item_name = playlist_map[item_id]['name']
                        playlist_pos = playlist_map[item_id]['position']
                        queue_list.append(f"• **{item_name}** (playlist #{playlist_pos}, queue #{info['queue_order']})")
                    else:
                        # Fallback if item not found in current playlist
                        item_name = info.get('item_name', 'Unknown')
                        queue_list.append(f"• **{item_name}** (queue #{info['queue_order']})")
                
                embed.add_field(
                    name="Active Queue Items",
                    value="\n".join(queue_list[:5]) + ("\n..." if len(queue_list) > 5 else ""),
                    inline=False
                )
            else:
                embed.add_field(
                    name="Active Queue Items",
                    value="No items currently queued",
                    inline=False
                )
            
            
            # Usage hint
            embed.add_field(
                name="Usage",
                value=f"Use {format_cmd_inline('queue_next <number>')} to queue a playlist item to play next",
                inline=False
            )
            
            await ctx.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Error in queue_status command: {e}")
            await ctx.send(f'Error getting queue status: {str(e)}')

    @commands.command(name='clear_queue', aliases=['qclear'])
    @commands.has_any_role(*Config.ALLOWED_ROLES)
    async def clear_queue(self, ctx):
        """Clear all queue tracking (useful for reset)"""
        try:
            self.vlc.clear_queue_tracking()
            embed = discord.Embed(
                title="🗑️ Queue Cleared",
                description="All queue tracking has been cleared",
                color=discord.Color.orange()
            )
            embed.add_field(
                name="Note",
                value="This only clears tracking data. Items already moved in the playlist remain in their positions.",
                inline=False
            )
            await ctx.send(embed=embed)
            logger.info("Queue tracking cleared by user command")
            
        except Exception as e:
            logger.error(f"Error in clear_queue command: {e}")
            await ctx.send(f'Error clearing queue: {str(e)}')

    @commands.command(name='remove_queue', aliases=['qremove','unqueue'])
    @commands.has_any_role(*Config.ALLOWED_ROLES)
    async def remove_queue(self, ctx, ref: str):
        """Remove a queued entry by queue order (e.g., 1) or playlist number (#10).

        Usage:
        - remove_queue 1           # remove by queue order
        - remove_queue #10         # remove by playlist number

        (Commands are shown with the configured prefix in the help output.)
        """
        try:
            if ref.startswith('#'):
                num = int(ref[1:])
                result = self.vlc.remove_from_queue_by_playlist_number(num)
            else:
                num = int(ref)
                result = self.vlc.remove_from_queue_by_order(num)

            if not result.get('success'):
                await ctx.send(f"❌ {result.get('error', 'Failed to remove from queue')}")
                return

            name = result.get('item_name', 'Unknown')
            embed = discord.Embed(
                title="✅ Removed from Queue",
                description=f"{name} has been removed from the queue.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
        except ValueError:
            await ctx.send("❌ Invalid reference. Use a number (queue order) or #<playlist number>.")
        except Exception as e:
            logger.error(f"Error in remove_queue command: {e}")
            await ctx.send(f"Error removing from queue: {str(e)}")

    @commands.has_any_role(*Config.ALLOWED_ROLES)
    @commands.command(name='shuffle_on', aliases=['shuffle_enable'])
    async def shuffle_on(self, ctx):
        """Enable shuffle mode"""
        try:
            current_shuffle = self.vlc.get_shuffle_state()
            
            if current_shuffle:
                embed = discord.Embed(
                    title="🔀 Shuffle Already On",
                    description="Shuffle mode is already enabled",
                    color=discord.Color.blue()
                )
            else:
                # Enable shuffle
                self.vlc.toggle_shuffle()
                embed = discord.Embed(
                    title="🔀 Shuffle Enabled",
                    description="Shuffle mode has been turned on",
                    color=discord.Color.green()
                )
            
            await ctx.send(embed=embed)
            logger.info(f"Shuffle enable command used by {ctx.author} (was already {'on' if current_shuffle else 'off'})")
            
        except Exception as e:
            logger.error(f"Error in shuffle_on command: {e}")
            await ctx.send(f'Error enabling shuffle: {str(e)}')

    @commands.has_any_role(*Config.ALLOWED_ROLES)
    @commands.command(name='shuffle_off', aliases=['shuffle_disable'])
    async def shuffle_off(self, ctx):
        """Disable shuffle mode"""
        try:
            current_shuffle = self.vlc.get_shuffle_state()
            
            if not current_shuffle:
                embed = discord.Embed(
                    title="▶️ Shuffle Already Off",
                    description="Shuffle mode is already disabled",
                    color=discord.Color.blue()
                )
            else:
                # Disable shuffle
                self.vlc.toggle_shuffle()
                embed = discord.Embed(
                    title="▶️ Shuffle Disabled",
                    description="Shuffle mode has been turned off",
                    color=discord.Color.green()
                )
            
            await ctx.send(embed=embed)
            logger.info(f"Shuffle disable command used by {ctx.author} (was already {'on' if current_shuffle else 'off'})")
            
        except Exception as e:
            logger.error(f"Error in shuffle_off command: {e}")
            await ctx.send(f'Error disabling shuffle: {str(e)}')

    @commands.has_any_role(*Config.ALLOWED_ROLES)
    @commands.command(name='shuffle_toggle', aliases=['shuffle'])
    async def shuffle_toggle(self, ctx):
        """Toggle shuffle mode on/off"""
        try:
            current_shuffle = self.vlc.get_shuffle_state()
            
            # Toggle shuffle
            self.vlc.toggle_shuffle()
            new_shuffle = not current_shuffle
            
            if new_shuffle:
                embed = discord.Embed(
                    title="🔀 Shuffle Enabled",
                    description="Shuffle mode has been turned on",
                    color=discord.Color.green()
                )
            else:
                embed = discord.Embed(
                    title="▶️ Shuffle Disabled",
                    description="Shuffle mode has been turned off",
                    color=discord.Color.green()
                )
            
            await ctx.send(embed=embed)
            logger.info(f"Shuffle toggle command used by {ctx.author} (changed from {'on' if current_shuffle else 'off'} to {'on' if new_shuffle else 'off'})")
            
        except Exception as e:
            logger.error(f"Error in shuffle_toggle command: {e}")
            await ctx.send(f'Error toggling shuffle: {str(e)}')
