from typing import Optional, List, Tuple
import discord
from discord.ext import commands
import os
import logging
import re
from ..utils.media_utils import MediaUtils
from ..config import Config
from ..utils.command_utils import format_cmd, format_cmd_inline

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
    def __init__(self, bot: commands.Bot, vlc_controller, tmdb_service, watch_service):
        self.bot = bot
        self.vlc = vlc_controller
        self.tmdb = tmdb_service
        self.watch_service = watch_service

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

    def _normalize_search_text(self, text: str) -> Tuple[str, str, List[str]]:
        """Normalize text for fuzzy search matching.

        Returns:
            Tuple of (normalized_with_spaces, compact_alnum, tokens)
        """
        lowered = (text or '').lower()
        # Convert common separators to spaces first, then strip other punctuation.
        normalized = re.sub(r'[._\-]+', ' ', lowered)
        normalized = re.sub(r'[^a-z0-9]+', ' ', normalized)
        normalized = ' '.join(normalized.split())
        compact = normalized.replace(' ', '')
        tokens = normalized.split() if normalized else []
        return normalized, compact, tokens

    def _score_match(self, query_norm: str, query_compact: str, query_tokens: List[str], item_name: str) -> int:
        """Return a relevance score for query vs item name.

        Higher score means better match.
        """
        item_norm, item_compact, _ = self._normalize_search_text(item_name)
        if not item_norm:
            return 0

        score = 0

        # Strong exact matches
        if query_norm == item_norm:
            score += 1000
        if query_compact and query_compact == item_compact:
            score += 900

        # Strong substring matches
        if query_norm and query_norm in item_norm:
            score += 700
        if query_compact and query_compact in item_compact:
            score += 650

        # Token coverage and order
        if query_tokens:
            matched = sum(1 for token in query_tokens if token in item_norm)
            score += matched * 80
            if matched == len(query_tokens):
                score += 120

            # Prefer exact token sequence starts (prefix-like behavior)
            item_tokens = item_norm.split()
            if len(query_tokens) <= len(item_tokens) and item_tokens[:len(query_tokens)] == query_tokens:
                score += 160

        # Small preference for tighter titles (reduces noisy broad matches)
        try:
            score -= abs(len(item_compact) - len(query_compact))
        except Exception:
            pass

        return max(0, score)

    def _search_items(self, query: str) -> List[Tuple[int, dict]]:
        """Search for items in the playlist
        
        Args:
            query: Search terms
            
        Returns:
            List of tuples (position, item) where position is 1-based index
        """
        items = self._get_playlist_items()
        query_norm, query_compact, query_tokens = self._normalize_search_text(query)
        if not query_tokens:
            return []

        scored_results: List[Tuple[int, int, dict]] = []
        for i, item in enumerate(items):
            raw_name = item.get('name', '')
            item_norm, item_compact, _ = self._normalize_search_text(raw_name)

            # Accept multiple match strategies to handle spacing/punctuation variants.
            matches = (
                (query_norm and query_norm in item_norm) or
                (query_compact and query_compact in item_compact) or
                all(token in item_norm for token in query_tokens)
            )

            if matches:
                score = self._score_match(query_norm, query_compact, query_tokens, raw_name)
                scored_results.append((score, i + 1, item))

        # Rank by best score first; use playlist position as stable tiebreaker.
        scored_results.sort(key=lambda r: (-r[0], r[1]))

        return [(pos, itm) for _, pos, itm in scored_results]

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
                embed.set_footer(text=f"💡 Use {format_cmd_inline('play_num <number>')} to play an item")
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
                hint = ""
                if len(results) > 1:
                    hint = f"\n💡 Top match selected from {len(results)} results."
                await ctx.send(f'Loading item #{playlist_num}...{hint}')
                
                # Get parsed title and optional year for metadata search
                search_title, search_year = MediaUtils.parse_movie_filename(item.get('name'))
                movie_embed = self.tmdb.get_movie_metadata(search_title, search_year)
                edition_tag = MediaUtils.extract_edition_tag(item.get('name'))
                
                if movie_embed:
                    if edition_tag:
                        try:
                            movie_embed.add_field(name="Edition", value=edition_tag, inline=True)
                        except Exception:
                            pass
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
            
            # Add media library size to footer
            size_bytes = self.watch_service.get_total_media_size() if self.watch_service else 0
            def human_size(num):
                for unit in ['B','KB','MB','GB','TB']:
                    if num < 1024.0:
                        return f"{num:.2f} {unit}"
                    num /= 1024.0
                return f"{num:.2f} PB"
            embed.set_footer(text=f"📑 Showing items 1-{len(current_items)} of {len(items)} | Media Library Size: {human_size(size_bytes)}")
            await ctx.send(embed=embed, view=view)
            
        except Exception as e:
            await ctx.send(f'Error listing playlist: {str(e)}')
