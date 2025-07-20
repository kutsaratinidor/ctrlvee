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
    def __init__(self, bot, vlc_controller, tmdb_service):
        self.bot = bot
        self.vlc = vlc_controller
        self.tmdb = tmdb_service
        self.last_state_change = {}
        self.logger = logging.getLogger(__name__)
        self.last_known_state = None
        self.last_known_position = None
        self.monitoring_task = None
        self.notification_channel = None
        
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
                    
            except Exception as e:
                logger.error(f"Error in VLC monitoring task: {e}")
            
            # Wait before next check
            await asyncio.sleep(1)  # Check every second

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

    @commands.command(name='rewind')
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
        """Play next track in playlist"""
        if not await self._check_cooldown(ctx):
            return
            
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
            search_title = MediaUtils.clean_movie_title(name)
            movie_embed = self.tmdb.get_movie_metadata(search_title)
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
        
        if state != 'stopped':
            # Initialize embed as None, we'll either use the movie_data embed or create a basic one
            embed = None
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
                search_title = MediaUtils.clean_movie_title(name)
                logger.debug(f"Status - Cleaned title: {search_title}")
                movie_data = self.tmdb.get_movie_metadata(search_title)
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
                    # Use the movie_data embed directly
                    embed = movie_data
                    
                    # Add state information
                    embed.insert_field_at(0, name="State", value=state.capitalize(), inline=True)
                else:
                    logger.debug("Status - No movie data, using filename")
                    # Create basic embed since we don't have movie data
                    embed = discord.Embed(
                        title="VLC Status",
                        color=discord.Color.blue()
                    )
                    embed.add_field(name="State", value=state.capitalize(), inline=True)
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
        
        await ctx.send(embed=embed)
