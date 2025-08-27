import discord
from discord.ext import commands
import asyncio
import logging
import xml.etree.ElementTree as ET
from ..utils.media_utils import MediaUtils
from ..config import Config

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
        self.notification_channel = None
        self.last_queue_auto_play = 0  # Timestamp of last queue auto-play to prevent rapid triggers
        
    async def cog_load(self):
        """Called when the cog is loaded"""
        self.monitoring_task = self.bot.loop.create_task(self._monitor_vlc_state())
        self.logger.info("VLC state monitoring started")
        
    async def cog_unload(self):
        """Called when the cog is unloaded"""
        if self.monitoring_task:
            self.monitoring_task.cancel()
            self.logger.info("VLC state monitoring stopped")
        
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
        
    @commands.command(name='set_notification_channel')
    @commands.has_any_role(*Config.ALLOWED_ROLES)
    async def set_notification_channel(self, ctx):
        """Set the current channel for VLC state change notifications"""
        self.notification_channel = ctx.channel
        logger.info(f"Notification channel set to {ctx.channel.name}")
        await ctx.send("✅ This channel will now receive VLC state change notifications")
        
    @commands.command(name='unset_notification_channel')
    @commands.has_any_role(*Config.ALLOWED_ROLES)
    async def unset_notification_channel(self, ctx):
        """Disable VLC state change notifications"""
        if self.notification_channel:
            logger.info("Notification channel unset")
            self.notification_channel = None
            await ctx.send("✅ VLC state change notifications have been disabled")
        else:
            await ctx.send("ℹ️ Notifications were already disabled")
            
    @commands.command(name='show_notification_channel', aliases=['notification_status'])
    @commands.has_any_role(*Config.ALLOWED_ROLES)
    async def show_notification_channel(self, ctx):
        """Show the current notification channel status"""
        if self.notification_channel:
            embed = discord.Embed(
                title="VLC Notification Status",
                description="Notifications are currently enabled",
                color=discord.Color.green()
            )
            embed.add_field(
                name="Channel",
                value=f"#{self.notification_channel.name}",
                inline=True
            )
            embed.add_field(
                name="Server",
                value=self.notification_channel.guild.name,
                inline=True
            )
        else:
            embed = discord.Embed(
                title="VLC Notification Status",
                description="Notifications are currently disabled",
                color=discord.Color.red()
            )
            embed.add_field(
                name="How to Enable",
                value="Use `!set_notification_channel` in the channel where you want to receive notifications",
                inline=False
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
                                                    if self.notification_channel:
                                                        try:
                                                            embed = discord.Embed(
                                                                title="🎵 Auto-Queue",
                                                                description=f"Automatically playing queued item: **{play_result.get('item_name', 'Unknown')}**",
                                                                color=discord.Color.green()
                                                            )
                                                            await self.notification_channel.send(embed=embed)
                                                        except Exception as e:
                                                            logger.error(f"Failed to send auto-queue notification: {e}")
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
                                                if self.notification_channel:
                                                    try:
                                                        embed = discord.Embed(
                                                            title="🎵 Auto-Queue",
                                                            description=f"Automatically playing queued item: **{play_result.get('item_name', 'Unknown')}**",
                                                            color=discord.Color.green()
                                                        )
                                                        await self.notification_channel.send(embed=embed)
                                                    except Exception as e:
                                                        logger.error(f"Failed to send auto-queue notification: {e}")
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
                                                if self.notification_channel:
                                                    try:
                                                        embed = discord.Embed(
                                                            title="🔀 Queue Management",
                                                            description="Shuffle mode automatically restored after queued item finished",
                                                            color=discord.Color.blue()
                                                        )
                                                        await self.notification_channel.send(embed=embed)
                                                    except Exception as e:
                                                        logger.error(f"Failed to send queue notification: {e}")
                                    
                                except Exception as e:
                                    logger.error(f"Error handling queue transition: {e}")
                            
                            # Detect when the last playing item finished (for shuffle restoration)
                            if position_changed and self.last_known_playing_item:
                                last_item_id = self.last_known_playing_item.get('id')
                                if last_item_id and last_item_id != current_item_id:
                                    # The last playing item is no longer playing - it finished
                                    try:
                                        self.vlc._handle_queued_item_finished(last_item_id)
                                    except Exception as e:
                                        logger.error(f"Error handling finished item {last_item_id}: {e}")
                        
                        if state_changed or position_changed:
                            # Get item name if available
                            item_name = None
                            if current_item is not None:
                                item_name = current_item.get('name')
                                
                            # Log the change regardless of notification channel
                            if state_changed:
                                logger.info(f"VLC state changed to: {current_state} (notifications {'enabled' if self.notification_channel else 'disabled'})")
                            elif position_changed:
                                logger.info(f"Track changed to: {item_name or 'Unknown'} #{current_position if current_position else 'N/A'} (notifications {'enabled' if self.notification_channel else 'disabled'})")
                            
                            # Only send Discord message if notification channel is set
                            if self.notification_channel:
                                # Create notification message
                                if state_changed:
                                    message = f"VLC state changed to: **{current_state}**"
                                elif position_changed:
                                    message = f"Track changed to: **{item_name or 'Unknown'}**"
                                    if current_position:
                                        message += f" (#{current_position})"
                                
                                # Send notification
                                embed = discord.Embed(
                                    title="VLC Manual Update",
                                    description=message,
                                    color=discord.Color.yellow()
                                )
                                
                                try:
                                    await self.notification_channel.send(embed=embed)
                                    logger.info(f"Manual VLC update detected: {message}")
                                except Exception as e:
                                    logger.error(f"Failed to send notification: {e}")
                    
                    # Update last known state
                    self.last_known_state = current_state
                    self.last_known_position = current_position
                    self.last_known_playing_item = current_item  # Track the current playing item
                    
                    # Priority 3: End-of-track detection - check if current track is about to end
                    if current_state in ['playing', 'paused'] and self.vlc.get_next_queued_item():
                        if self._check_queue_auto_play_cooldown():
                            try:
                                status = self.vlc.get_status()
                                if status is not None:
                                    time_elem = status.find('time')
                                    length_elem = status.find('length')
                                    if time_elem is not None and length_elem is not None:
                                        current_time = int(time_elem.text)
                                        total_length = int(length_elem.text)
                                        
                                        # If we're within 2 seconds of the end and have a queued item, auto-play it
                                        if total_length > 0 and (total_length - current_time) <= 2 and current_time > 0:
                                            logger.info(f"Detected track near end ({current_time}/{total_length}s) - checking queue")
                                            
                                            next_queued = self.vlc.get_next_queued_item()
                                            if next_queued:
                                                logger.info(f"Track ending, auto-playing queued item: {next_queued}")
                                                
                                                play_result = self.vlc.play_next_queued_item()
                                                if play_result.get("success"):
                                                    logger.info(f"Successfully auto-played queued item at track end: {play_result.get('item_name', 'Unknown')}")
                                                    
                                                    # Optionally notify in Discord if notification channel is set
                                                    if self.notification_channel:
                                                        try:
                                                            embed = discord.Embed(
                                                                title="🎵 Auto-Queue (End Detection)",
                                                                description=f"Track ended, playing queued item: **{play_result.get('item_name', 'Unknown')}**",
                                                                color=discord.Color.green()
                                                            )
                                                            await self.notification_channel.send(embed=embed)
                                                        except Exception as e:
                                                            logger.error(f"Failed to send end detection notification: {e}")
                            except Exception as e:
                                logger.debug(f"Error in end-of-track detection: {e}")
                        else:
                            logger.debug("End-of-track queue auto-play skipped due to cooldown")
                    
                    # Enhanced periodic check: If we have queued items, ensure they get played
                    next_queued = self.vlc.get_next_queued_item()
                    if next_queued:
                        # Case 1: VLC is stopped and we have queued items
                        if current_state == 'stopped':
                            if self._check_queue_auto_play_cooldown():
                                try:
                                    play_result = self.vlc.play_next_queued_item()
                                    
                                    if play_result.get("success"):
                                        logger.info(f"Periodic auto-play successful: {play_result.get('item_name', 'Unknown')}")
                                    else:
                                        logger.warning(f"Periodic auto-play failed: {play_result.get('error', 'Unknown error')}")
                                except Exception as e:
                                    logger.error(f"Error in periodic queue check: {e}")
                        
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
                                    except Exception as e:
                                        logger.error(f"Error in periodic queue correction: {e}")
                    
            except Exception as e:
                logger.error(f"Error in VLC monitoring task: {e}")
            
            # Wait before next check
            await asyncio.sleep(0.5)  # Check every half second for more responsive queue handling

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
                await ctx.send('Playback started/resumed')

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
            await ctx.send('Playback stopped')
        else:
            logger.error("Failed to stop playback")
            await ctx.send('Error: Could not stop playback')

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
            await ctx.send('Please specify a positive number of seconds')
            return

        if self.vlc.seek(f"-{seconds}"):
            await ctx.send(f'Rewound {seconds} seconds')
        else:
            await ctx.send('Error: Could not rewind')
 
    @commands.command(name='forward', aliases=['ff'])
    @commands.has_any_role(*Config.ALLOWED_ROLES)
    async def forward(self, ctx, seconds: int = 10):
        """Fast forward playback by specified number of seconds"""
        if seconds <= 0:
            await ctx.send('Please specify a positive number of seconds')
            return

        if self.vlc.seek(f"+{seconds}"):
            await ctx.send(f'Fast forwarded {seconds} seconds')
        else:
            await ctx.send('Error: Could not fast forward')
    
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
                
                # Update now playing with the item we just started
                await self._update_now_playing(ctx, item, number)
                
                # Verify it's actually playing
                status = self.vlc.get_status()
                if status and status.find('state').text != 'playing':
                    await ctx.send("Warning: VLC might not be playing. Try using !play if playback doesn't start.")
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
                return
            else:
                await ctx.send(f"Error playing queued item: {result.get('error', 'Unknown error')}")
                # Fall through to normal next behavior
        
        # If no queued items or queue failed, use normal next behavior
        if self.vlc.next():
            logger.info("Loading next track")
            await ctx.send('Loading next track...')
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
                    await self._update_now_playing(ctx, current_item, position)
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
                    await self._update_now_playing(ctx, current_item, position)
                else:
                    await ctx.send('Jumped to previous track')
            else:
                await ctx.send('Jumped to previous track')
        else:
            await ctx.send('Error: Could not jump to previous track')

    async def _update_now_playing(self, ctx, item, item_number=None):
        """Update the now playing message with enhanced metadata"""
        try:
            name = item.get("name")
            if not name:
                logger.error("Invalid item - no name found")
                await ctx.send('Error: Invalid item (no name found)')
                return
                
            logger.info(f"Now playing: {name}" + (f" (#{item_number})" if item_number else ""))
            search_title, search_year = MediaUtils.parse_movie_filename(name)
            movie_embed = self.tmdb.get_movie_metadata(search_title, search_year)
        except Exception as e:
            logger.error(f"Error getting movie metadata: {str(e)}")
            await ctx.send(f'Error getting movie metadata: {str(e)}')
            return
        
        if movie_embed:
            # For movie metadata, add Quick Replay at the bottom
            if item_number:
                movie_embed.add_field(name="Quick Replay", value=f"💡 Use **!play_num {item_number}** to play this item again", inline=False)
            await ctx.send(embed=movie_embed)
        else:
            embed = discord.Embed(
                title="Now Playing",
                color=discord.Color.blue()
            )
            
            # Add name
            embed.add_field(
                name="Now Playing",
                value=name,
                inline=False
            )
            
            # Add Quick Replay at the bottom
            if item_number:
                embed.add_field(name="Quick Replay", value=f"💡 Use **!play_num {item_number}** to play this item again", inline=False)
                
            await ctx.send(embed=embed)
            
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
            
    @commands.command(name='status')
    async def status(self, ctx):
        """Show current VLC status with enhanced metadata"""
        if not await self._check_vlc_connection(ctx):
            return
            
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
                    embed.add_field(name="Quick Replay", value=f"💡 Use **!play_num {current_position}** to play this item again", inline=False)
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
                        value=f"{playlist_count} items in playlist\nUse `!play` to resume or `!play_num <number>` to play a specific item", 
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
                value="Use `!queue_next <number>` to queue a playlist item to play next",
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
