from typing import List, Tuple, Optional
import json
import os
import logging
import discord
from discord.ext import commands
from ..utils.media_utils import MediaUtils
from ..config import Config

class QueueManager:
    """Manages the playlist queue with persistence"""
    
    def __init__(self, queue_file: str = "queue_backup.json"):
        """Initialize queue manager
        
        Args:
            queue_file: Path to queue backup file
        """
        self.queue: List[Tuple[str, str, int, int]] = []  # (item_id, item_name, requester_id, playlist_index)
        self.queue_file = queue_file
        self.logger = logging.getLogger(__name__)
        self.load_queue()
        
    def save_queue(self) -> None:
        """Save current queue to file"""
        try:
            with open(self.queue_file, 'w') as f:
                queue_data = [
                    {
                        'item_id': item_id,
                        'item_name': item_name,
                        'requester_id': requester_id,
                        'playlist_index': playlist_index
                    }
                    for item_id, item_name, requester_id, playlist_index in self.queue
                ]
                json.dump(queue_data, f, indent=2)
        except Exception as e:
            self.logger.error(f"Error saving queue: {e}")
            
    def load_queue(self) -> None:
        """Load queue from file if it exists"""
        try:
            if os.path.exists(self.queue_file) and os.path.getsize(self.queue_file) > 0:
                with open(self.queue_file, 'r') as f:
                    queue_data = json.load(f)
                    self.queue = [
                        (item['item_id'], item['item_name'], int(item['requester_id']), item['playlist_index'])
                        for item in queue_data
                    ]
                self.logger.info(f"Loaded {len(self.queue)} items from queue backup")
        except Exception as e:
            self.logger.error(f"Error loading queue: {e}")
            self.queue = []
        
    def add(self, item_id: str, item_name: str, requester_id: int, playlist_index: int) -> None:
        """Add an item to the queue"""
        self.queue.append((item_id, item_name, requester_id, playlist_index))
        self.save_queue()
        
    def remove(self, index: int) -> Optional[Tuple[str, str, int, int]]:
        """Remove an item from the queue by index"""
        if 0 <= index < len(self.queue):
            item = self.queue.pop(index)
            self.save_queue()
            return item
        return None
        
    def get_next(self) -> Optional[Tuple[str, str, int, int]]:
        """Get the next item in the queue"""
        return self.queue[0] if self.queue else None
        
    def clear(self) -> None:
        """Clear the queue"""
        self.queue.clear()
        self.save_queue()
        try:
            if os.path.exists(self.queue_file):
                os.remove(self.queue_file)
        except Exception as e:
            self.logger.error(f"Error removing queue backup file: {e}")
        
    def is_empty(self) -> bool:
        """Check if queue is empty"""
        return len(self.queue) == 0
        
    def get_list(self) -> List[Tuple[str, str, int, int]]:
        """Get the current queue list"""
        return self.queue.copy()

class QueueCommands(commands.Cog):
    """Discord commands for queue management"""
    
    def __init__(self, bot: commands.Bot, vlc_controller, tmdb_service):
        """Initialize queue commands
        
        Args:
            bot: Discord bot instance
            vlc_controller: VLC controller instance
            tmdb_service: TMDB service instance
        """
        self.bot = bot
        self.vlc = vlc_controller
        self.tmdb = tmdb_service
        self.queue_manager = QueueManager()

    @commands.command(name='queue')
    async def show_queue(self, ctx: commands.Context):
        """Show the current queue"""
        if self.queue_manager.is_empty():
            await ctx.send('Queue is empty')
            return
            
        queue = self.queue_manager.get_list()
        queue_text = ""
        for i, (item_id, item_name, requester_id, playlist_index) in enumerate(queue, 1):
            try:
                requester = ctx.guild.get_member(requester_id)
                if not requester:
                    requester = await ctx.guild.fetch_member(requester_id)
                requester_name = requester.display_name if requester else f"Unknown (ID: {requester_id})"
            except Exception as e:
                self.logger.error(f"Error getting member {requester_id}: {e}")
                requester_name = f"Unknown (ID: {requester_id})"
                
            basename = MediaUtils.clean_filename_for_display(item_name)
            queue_text += f"{i}. #{playlist_index} - {basename} (added by {requester_name})\n"
        
        embed = discord.Embed(
            title="Current Queue",
            description=queue_text,
            color=discord.Color.blue()
        )
        await ctx.send(embed=embed)

    @commands.command(name='clearq')
    @commands.has_any_role(*Config.ALLOWED_ROLES)
    async def clear_queue(self, ctx):
        """Clear the queue"""
        self.queue_manager.clear()
        await ctx.send('Queue cleared')

    @commands.command(name='addq')
    @commands.has_any_role(*Config.ALLOWED_ROLES)
    async def add_to_queue(self, ctx, number: int):
        """Add an item from the playlist to the queue"""
        try:
            if number < 1:
                await ctx.send('Please provide a number greater than 0')
                return
                
            playlist_xml = self.vlc.get_playlist()
            if playlist_xml is not None:
                items = playlist_xml.findall('.//leaf')
                if not items:
                    await ctx.send('Playlist is empty')
                    return
                    
                if number > len(items):
                    await ctx.send(f'Number too high. Playlist has {len(items)} items')
                    return
                
                item = items[number - 1]
                item_id = item.get('id')
                item_name = item.get('name')
                
                self.queue_manager.add(item_id, item_name, ctx.author.id, number)
                basename = MediaUtils.clean_filename_for_display(item_name)
                await ctx.send(f'Added to queue: #{number} {basename}')
            else:
                await ctx.send('Could not access VLC playlist')
        except ValueError:
            await ctx.send('Please provide a valid number')

    @commands.command(name='removeq')
    @commands.has_any_role(*Config.ALLOWED_ROLES)
    async def remove_from_queue(self, ctx, number: int):
        """Remove an item from the queue"""
        try:
            if number < 1:
                await ctx.send('Please provide a number greater than 0')
                return
                
            if number > len(self.queue_manager.queue):
                await ctx.send(f'Number too high. Queue has {len(self.queue_manager.queue)} items')
                return
                
            removed = self.queue_manager.remove(number - 1)
            if removed:
                basename = MediaUtils.clean_filename_for_display(removed[1])
                await ctx.send(f'Removed from queue: {basename}')
            else:
                await ctx.send('Could not remove item from queue')
        except ValueError:
            await ctx.send('Please provide a valid number')

    @commands.command(name='playq')
    @commands.has_any_role(*Config.ALLOWED_ROLES)
    async def play_queue(self, ctx: commands.Context):
        """Play the next item in the queue"""
        next_item = self.queue_manager.get_next()
        if next_item is None:
            await ctx.send('Queue is empty')
            return
            
        item_id, item_name, _, _ = next_item
        if self.vlc.play_item(item_id):
            self.queue_manager.remove(0)
            
            search_title = MediaUtils.clean_movie_title(item_name)
            movie_embed = self.tmdb.get_movie_metadata(search_title)
            
            if movie_embed:
                movie_embed.set_footer(text="Playing from queue")
                await ctx.send(embed=movie_embed)
            else:
                basename = MediaUtils.clean_filename_for_display(item_name)
                await ctx.send(f'Playing from queue: {basename}')
        else:
            await ctx.send('Error: Could not play the queued item')
