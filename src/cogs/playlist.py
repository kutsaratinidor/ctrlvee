from typing import Optional, List, Tuple
import discord
from discord.ext import commands
import os
import logging
from ..utils.media_utils import MediaUtils
from ..config import Config

logger = logging.getLogger(__name__)

class PlaylistView(discord.ui.View):
    def __init__(self, items, items_per_page: Optional[int] = None):
        super().__init__(timeout=300)  # 5 minute timeout
        self.items = items
        # Default to Config if not provided
        self.items_per_page = items_per_page or Config.ITEMS_PER_PAGE
        self.current_page = 1
        self.total_pages = (len(items) + self.items_per_page - 1) // self.items_per_page

    @discord.ui.button(label="⏮️", style=discord.ButtonStyle.secondary)
    async def first_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = 1
        await self.update_message(interaction)

    @discord.ui.button(label="◀️", style=discord.ButtonStyle.primary)
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = max(1, self.current_page - 1)
        await self.update_message(interaction)

    @discord.ui.button(label="📖", style=discord.ButtonStyle.success)
    async def goto_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = PageSelectModal(self.total_pages, self)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="▶️", style=discord.ButtonStyle.primary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = min(self.total_pages, self.current_page + 1)
        await self.update_message(interaction)

    @discord.ui.button(label="⏭️", style=discord.ButtonStyle.secondary)
    async def last_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = self.total_pages
        await self.update_message(interaction)

    async def update_message(self, interaction: discord.Interaction):
        """Update the playlist view message"""
        start_idx = (self.current_page - 1) * self.items_per_page
        end_idx = min(start_idx + self.items_per_page, len(self.items))
        current_items = self.items[start_idx:end_idx]

        embed = discord.Embed(
            title="VLC Playlist",
            color=discord.Color.blue()
        )
        
        playlist_text = ""
        for i, item in enumerate(current_items, start=start_idx + 1):
            name = item.get('name', '')
            icon = MediaUtils.get_media_icon(name)
            basename = MediaUtils.clean_filename_for_display(name)
            
            next_line = f"{icon}`{i}` {basename}\n"
            if len(playlist_text) + len(next_line) > 1000:
                playlist_text += "...(more items on next page)"
                break
                
            playlist_text += next_line
        
        embed.add_field(
            name=f"📋 Page {self.current_page}/{self.total_pages}",
            value=playlist_text if playlist_text else "No items in playlist",
            inline=False
        )
        
        embed.set_footer(text=f"📑 Showing items {start_idx + 1}-{end_idx} of {len(self.items)}")
        await interaction.response.edit_message(embed=embed, view=self)

class PageSelectModal(discord.ui.Modal, title='Go to Page'):
    def __init__(self, total_pages: int, view: PlaylistView):
        super().__init__()
        self.total_pages = total_pages
        self.view = view
        
        self.page_input = discord.ui.TextInput(
            label=f'Enter page number (1-{total_pages})',
            placeholder='Enter a number',
            min_length=1,
            max_length=len(str(total_pages)),
            required=True
        )
        self.add_item(self.page_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            page = int(self.page_input.value)
            if 1 <= page <= self.total_pages:
                self.view.current_page = page
                await self.view.update_message(interaction)
            else:
                await interaction.response.send_message(
                    f"Please enter a valid page number between 1 and {self.total_pages}",
                    ephemeral=True
                )
        except ValueError:
            await interaction.response.send_message(
                "Please enter a valid number",
                ephemeral=True
            )

class PlaylistCommands(commands.Cog):
    def __init__(self, bot: commands.Bot, vlc_controller, tmdb_service):
        self.bot = bot
        self.vlc = vlc_controller
        self.tmdb = tmdb_service

    def _get_playlist_items(self) -> List[dict]:
        """Get current playlist items"""
        playlist_xml = self.vlc.get_playlist()
        if playlist_xml is not None:
            return playlist_xml.findall('.//leaf')
        return []

    def _find_item_by_id(self, item_id: str) -> Tuple[Optional[dict], int]:
        """Find an item and its index in the playlist by ID"""
        items = self._get_playlist_items()
        for i, item in enumerate(items):
            if item.get('id') == item_id:
                return item, i
        return None, -1

    def _search_items(self, query: str) -> List[Tuple[int, dict]]:
        """Search for items in the playlist
        
        Args:
            query: Search terms
            
        Returns:
            List of tuples (position, item) where position is 1-based index
        """
        items = self._get_playlist_items()
        search_words = query.lower().split()
        # Item matches if ALL words in query are found in its name
        return [(i+1, item) for i, item in enumerate(items)
                if all(word in item.get('name', '').lower() for word in search_words)]

    @commands.command(name='search')
    @commands.has_any_role(*Config.ALLOWED_ROLES)
    async def search_playlist(self, ctx: commands.Context, *, query: str):
        """Search for items in the playlist"""
        try:
            results = self._search_items(query)
            
            # Create embed
            embed = discord.Embed(
                title="🔍 Search Results",
                description=f"Search query: '{query}'",
                color=discord.Color.blue()
            )
            
            if results:
                # Format results with proper icons and cleaned names
                results_text = ""
                for playlist_num, item in results:
                    name = item.get('name', '')
                    icon = MediaUtils.get_media_icon(name)
                    basename = MediaUtils.clean_filename_for_display(name)
                    results_text += f"{icon}`{playlist_num}` {basename}\n"
                
                # Add results to embed
                embed.add_field(
                    name=f"Found {len(results)} matches",
                    value=results_text if len(results_text) <= 1024 else results_text[:1021] + "...",
                    inline=False
                )
                embed.set_footer(text="💡 Use !play_num <number> to play an item")
            else:
                embed.add_field(
                    name="No Results",
                    value="No matches found in the playlist",
                    inline=False
                )
            
            await ctx.send(embed=embed)
        except Exception as e:
            logger.error(f"Error searching playlist: {e}")
            await ctx.send(f'Error searching playlist: {str(e)}')

    @commands.command(name='play_search')
    @commands.has_any_role(*Config.ALLOWED_ROLES)
    async def play_search(self, ctx: commands.Context, *, query: str):
        """Search for and play an item from the playlist"""
        try:
            results = self._search_items(query)
            if not results:
                await ctx.send('No matches found in playlist')
                return
                
            # Play the first match
            playlist_num, item = results[0]
            item_id = item.get('id')
            
            if self.vlc.play_item(item_id):
                logger.info(f"Playing search result: {item.get('name')} (#{playlist_num})")
                await ctx.send(f'Loading item #{playlist_num}...')
                
                # Get parsed title and optional year for metadata search
                search_title, search_year = MediaUtils.parse_movie_filename(item.get('name'))
                movie_embed = self.tmdb.get_movie_metadata(search_title, search_year)
                
                if movie_embed:
                    movie_embed.set_footer(text=f"Now Playing #{playlist_num}")
                    await ctx.send(embed=movie_embed)
                else:
                    name = item.get('name', '')
                    icon = MediaUtils.get_media_icon(name)
                    basename = MediaUtils.clean_filename_for_display(name)
                    await ctx.send(f'Playing: {icon}`{playlist_num}` {basename}')
            else:
                await ctx.send('Error: Could not play the selected item')
        except Exception as e:
            logger.error(f"Error in play_search: {e}")
            await ctx.send(f'Error searching and playing: {str(e)}')

    @commands.command(name='list')
    async def list_playlist(self, ctx: commands.Context):
        """List items in the playlist with interactive navigation"""
        try:
            items = self._get_playlist_items()
            if not items:
                await ctx.send('Playlist is empty')
                return

            view = PlaylistView(items, items_per_page=Config.ITEMS_PER_PAGE)
            
            # Create initial embed
            embed = discord.Embed(
                title="VLC Playlist",
                color=discord.Color.blue()
            )
            
            # Show first page
            current_items = items[:Config.ITEMS_PER_PAGE]
            playlist_text = ""
            for i, item in enumerate(current_items, start=1):
                name = item.get('name', '')
                icon = MediaUtils.get_media_icon(name)
                basename = MediaUtils.clean_filename_for_display(name)
                playlist_text += f"{icon}`{i}` {basename}\n"
            
            embed.add_field(
                name=f"📋 Page 1/{(len(items) + (Config.ITEMS_PER_PAGE - 1)) // Config.ITEMS_PER_PAGE}",
                value=playlist_text if playlist_text else "No items in playlist",
                inline=False
            )
            
            embed.set_footer(text=f"📑 Showing items 1-{len(current_items)} of {len(items)}")
            await ctx.send(embed=embed, view=view)
            
        except Exception as e:
            await ctx.send(f'Error listing playlist: {str(e)}')
