from typing import Optional, List, Tuple
import discord
from discord.ext import commands
import logging
import re
from ..utils.media_utils import MediaUtils
from ..config import Config
from ..utils.command_utils import format_cmd_inline

logger = logging.getLogger(__name__)

class PlaylistView(discord.ui.View):
    def __init__(self, items, items_per_page: Optional[int] = None):
        super().__init__(timeout=300)  # 5 minute timeout
        self.items = items
        # Default to Config if not provided
        self.items_per_page = max(1, items_per_page or Config.ITEMS_PER_PAGE)
        self.max_chars_per_page = 1000
        self.current_page = 1
        self.pages = self._build_pages()
        self.total_pages = max(1, len(self.pages))

    def _build_pages(self) -> List[List[str]]:
        """Paginate playlist lines by item-count and field character limits."""
        pages: List[List[str]] = []
        current_page: List[str] = []
        current_chars = 0

        for i, item in enumerate(self.items, start=1):
            name = item.get('name', '')
            icon = MediaUtils.get_media_icon(name)
            basename = MediaUtils.clean_filename_for_display(name)
            line = f"{icon}`{i}` {basename}"

            # Ensure one very long title still fits safely in a page.
            if len(line) > self.max_chars_per_page:
                keep = max(0, self.max_chars_per_page - len(f"{icon}`{i}` ..."))
                line = f"{icon}`{i}` {basename[:keep]}..."

            projected = current_chars + len(line) + (1 if current_page else 0)
            would_overflow_chars = projected > self.max_chars_per_page
            would_overflow_items = len(current_page) >= self.items_per_page

            if current_page and (would_overflow_chars or would_overflow_items):
                pages.append(current_page)
                current_page = []
                current_chars = 0

            current_page.append(line)
            current_chars += len(line) + (1 if len(current_page) > 1 else 0)

        if current_page:
            pages.append(current_page)

        return pages

    def build_embed(self) -> discord.Embed:
        page_idx = self.current_page - 1
        page_lines = self.pages[page_idx] if self.pages else []

        # Compute global displayed range for this page.
        start = sum(len(p) for p in self.pages[:page_idx]) + 1 if page_lines else 0
        end = start + len(page_lines) - 1 if page_lines else 0

        embed = discord.Embed(
            title="VLC Playlist",
            color=discord.Color.blue()
        )
        embed.add_field(
            name=f"📋 Page {self.current_page}/{self.total_pages}",
            value="\n".join(page_lines) if page_lines else "No items in playlist",
            inline=False
        )
        if start and end:
            embed.set_footer(text=f"📑 Showing items {start}-{end} of {len(self.items)}")
        else:
            embed.set_footer(text=f"📑 Showing items 0-0 of {len(self.items)}")
        return embed

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
        embed = self.build_embed()
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


class SearchResultsView(discord.ui.View):
    def __init__(self, query: str, pages: List[List[str]], total_matches: int):
        super().__init__(timeout=300)
        self.query = query
        self.pages = pages
        self.total_matches = total_matches
        self.current_page = 1
        self.total_pages = max(1, len(pages))

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
        page_idx = self.current_page - 1
        page_lines = self.pages[page_idx] if self.pages else []

        # Compute item range shown in this page for clearer navigation context.
        start = sum(len(p) for p in self.pages[:page_idx]) + 1 if page_lines else 0
        end = start + len(page_lines) - 1 if page_lines else 0

        embed = discord.Embed(
            title="🔍 Search Results",
            description=f"Search query: '{self.query}'",
            color=discord.Color.blue()
        )
        embed.add_field(
            name=f"Found {self.total_matches} matches • Page {self.current_page}/{self.total_pages}",
            value="\n".join(page_lines) if page_lines else "No matches on this page",
            inline=False
        )
        if start and end:
            footer = f"💡 Use {format_cmd_inline('play_num <number>')} to play an item • Showing {start}-{end} of {self.total_matches}"
        else:
            footer = f"💡 Use {format_cmd_inline('play_num <number>')} to play an item"
        embed.set_footer(text=footer)

        await interaction.response.edit_message(embed=embed, view=self)

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

    def _build_search_pages(self, results: List[Tuple[int, dict]]) -> List[List[str]]:
        """Paginate search lines while respecting Discord embed field limits.

        Pages are constrained by both max line count (`Config.ITEMS_PER_PAGE`) and
        max rendered characters to avoid hitting Discord's 1024-char field cap.
        """
        max_items_per_page = max(1, int(getattr(Config, 'ITEMS_PER_PAGE', 20)))
        max_chars_per_page = 1000

        pages: List[List[str]] = []
        current_page: List[str] = []
        current_chars = 0

        for playlist_num, item in results:
            name = item.get('name', '')
            icon = MediaUtils.get_media_icon(name)
            basename = MediaUtils.clean_filename_for_display(name)
            line = f"{icon}`{playlist_num}` {basename}"

            # Truncate very long single lines so one item can always fit a page.
            if len(line) > max_chars_per_page:
                keep = max(0, max_chars_per_page - len(f"{icon}`{playlist_num}` ..."))
                line = f"{icon}`{playlist_num}` {basename[:keep]}..."

            projected = current_chars + len(line) + (1 if current_page else 0)
            would_overflow_chars = projected > max_chars_per_page
            would_overflow_items = len(current_page) >= max_items_per_page

            if current_page and (would_overflow_chars or would_overflow_items):
                pages.append(current_page)
                current_page = []
                current_chars = 0

            current_page.append(line)
            current_chars += len(line) + (1 if len(current_page) > 1 else 0)

        if current_page:
            pages.append(current_page)

        return pages

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
                pages = self._build_search_pages(results)
                first_page = pages[0] if pages else []
                embed.add_field(
                    name=f"Found {len(results)} matches • Page 1/{max(1, len(pages))}",
                    value="\n".join(first_page) if first_page else "No matches found in the playlist",
                    inline=False
                )
                shown = len(first_page)
                if shown:
                    embed.set_footer(
                        text=(
                            f"💡 Use {format_cmd_inline('play_num <number>')} to play an item "
                            f"• Showing 1-{shown} of {len(results)}"
                        )
                    )
                else:
                    embed.set_footer(text=f"💡 Use {format_cmd_inline('play_num <number>')} to play an item")

                if len(pages) > 1:
                    view = SearchResultsView(query=query, pages=pages, total_matches=len(results))
                    await ctx.send(embed=embed, view=view)
                else:
                    await ctx.send(embed=embed)
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
            embed = view.build_embed()
            
            # Add media library size to footer
            size_bytes = self.watch_service.get_total_media_size() if self.watch_service else 0
            def human_size(num):
                for unit in ['B','KB','MB','GB','TB']:
                    if num < 1024.0:
                        return f"{num:.2f} {unit}"
                    num /= 1024.0
                return f"{num:.2f} PB"
            # Preserve the page range and append media size.
            page_footer = embed.footer.text or ""
            embed.set_footer(text=f"{page_footer} | Media Library Size: {human_size(size_bytes)}")
            await ctx.send(embed=embed, view=view)
            
        except Exception as e:
            await ctx.send(f'Error listing playlist: {str(e)}')
