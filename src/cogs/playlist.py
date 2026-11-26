from typing import Optional, List, Tuple
import asyncio
import math
import random
import time
import discord
from discord.ext import commands, tasks
import logging
import re
from ..utils.media_utils import MediaUtils
from ..config import Config
from ..utils.command_utils import format_cmd_inline
from ..services.genre_service import match_genre, is_movie

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

SEARCH_PICKER_CAP = 25  # hard limit on Discord select options


def _picker_options(results) -> List[discord.SelectOption]:
    return [
        discord.SelectOption(
            label=f'#{num} {MediaUtils.clean_filename_for_display(item.get("name", ""), max_length=88)}',
            value=str(num),
        )
        for num, item in results
    ]


class PlaySearchView(discord.ui.View):
    """Picker for play-search (and genre lists) when multiple results match."""

    def __init__(self, cog, requester_id: int, results: List[Tuple[int, dict]]):
        super().__init__(timeout=120)
        self.cog = cog
        self.requester_id = requester_id
        self.message = None
        self.results = results[:SEARCH_PICKER_CAP]

        select = discord.ui.Select(placeholder='Choose an item to play', options=_picker_options(self.results))
        select.callback = self._on_select
        self.add_item(select)

        cancel = discord.ui.Button(label='✕ Cancel', style=discord.ButtonStyle.danger)
        cancel.callback = self._on_cancel
        self.add_item(cancel)

    def _disable_all(self) -> None:
        """Disable every child (select + buttons) so the picker becomes inert."""
        for child in self.children:
            child.disabled = True

    async def _on_cancel(self, interaction: discord.Interaction):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message('That picker belongs to someone else.', ephemeral=True)
            return
        self._disable_all()
        await interaction.response.edit_message(content='Playback selection cancelled.', embed=None, view=self)

    async def _picked(self, interaction: discord.Interaction):
        """(playlist number, item) chosen in a select, or None (someone else's picker, or stale)."""
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message('That picker belongs to someone else.', ephemeral=True)
            return None
        try:
            playlist_num = int(interaction.data['values'][0])
        except (KeyError, IndexError, TypeError, ValueError):
            return None
        return next((p for p in self.results if p[0] == playlist_num), None)

    async def _on_select(self, interaction: discord.Interaction):
        pair = await self._picked(interaction)
        if pair is None:
            return
        playlist_num, item = pair

        playback_cog = self.cog.bot.get_cog('PlaybackCommands')
        if playback_cog:
            ok, reason = playback_cog._playback_guard(interaction.user)
            if not ok:
                await interaction.response.send_message(reason, ephemeral=True)
                return

        # The schedule check runs on the pick, against the picked item's runtime. Its prompt
        # can wait on Continue/Cancel well past Discord's 3 s window, so defer first.
        await interaction.response.defer()
        if playback_cog and not await playback_cog._confirm_nearby_schedule(interaction.channel, interaction.user, item):
            return

        item_id = item.get('id')
        if not item_id or not self.cog.vlc.play_item(item_id):
            await interaction.followup.send('Could not play the selected item.', ephemeral=True)
            return

        if playback_cog:
            playback_cog._seat_playback_owner(interaction.user, item_id)

        pretty = MediaUtils.clean_filename_for_display(item.get('name', ''), max_length=120)
        await interaction.followup.send(f'Loading item #{playlist_num}: {pretty}')

        if playback_cog and hasattr(playback_cog, '_announce_now_playing'):
            try:
                await playback_cog._announce_now_playing('command', item, playlist_num)
            except Exception:
                pass

        self._disable_all()
        try:
            await interaction.message.edit(view=self)
        except Exception:
            pass

    async def on_timeout(self):
        self._disable_all()
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass

GENRE_PAGE_SIZE = 20  # under the 25-option select limit


class GenrePickerView(PlaySearchView):
    """A genre's movies a page at a time, with that page in the play picker."""

    def __init__(self, cog, requester_id: int, genre: str, results: List[Tuple[int, dict]]):
        super().__init__(cog, requester_id, results[:GENRE_PAGE_SIZE])
        # Queue select goes right under the play select, so the buttons stay on the last row
        cancel = self.children[-1]
        self.remove_item(cancel)
        queue = discord.ui.Select(placeholder='Choose a movie to queue next', options=_picker_options(self.results))
        queue.callback = self._on_queue
        self.add_item(queue)
        self.add_item(cancel)
        self.genre = genre
        self.all_results = results
        self.page = 0
        self.pages = max(1, math.ceil(len(results) / GENRE_PAGE_SIZE))
        if self.pages > 1:
            for label, step in (('◀️', -1), ('▶️', 1)):
                button = discord.ui.Button(label=label, style=discord.ButtonStyle.primary)
                button.callback = lambda interaction, step=step: self._turn(interaction, step)
                self.add_item(button)

    def build_embed(self) -> discord.Embed:
        lines = [
            f"`#{num}` {discord.utils.escape_markdown(MediaUtils.clean_filename_for_display(item.get('name', ''), max_length=60))}"
            for num, item in self.results
        ]
        embed = discord.Embed(title=f"🎭 {self.genre[:240]}", description="\n".join(lines), color=discord.Color.blurple())
        footer = f"{len(self.all_results)} movies"
        if self.pages > 1:
            footer += f" · page {self.page + 1}/{self.pages}"
        embed.set_footer(text=footer + " · pick one below to play now or queue next")
        return embed

    async def _on_queue(self, interaction: discord.Interaction):
        """Queue the pick to play after the current item (same checks as !queue_next). The
        picker stays open, so several movies can be queued from one list."""
        pair = await self._picked(interaction)
        if pair is None:
            return
        playlist_num, item = pair
        playback_cog = self.cog.bot.get_cog('PlaybackCommands')
        if playback_cog:
            ok, reason = playback_cog._playback_guard(interaction.user)
            if not ok:
                await interaction.response.send_message(reason, ephemeral=True)
                return
        await interaction.response.defer()
        if playback_cog:
            starts_in = await asyncio.to_thread(playback_cog._queue_next_starts_in)
            if not await playback_cog._confirm_nearby_schedule(interaction.channel, interaction.user, item, starts_in):
                return
        result = await asyncio.to_thread(self.cog.vlc.queue_item_next, item.get('id'))
        if not result.get('success'):
            await interaction.followup.send(f"Could not queue that: {result.get('error', 'unknown error')}", ephemeral=True)
            return
        pretty = MediaUtils.clean_filename_for_display(item.get('name', ''), max_length=120)
        await interaction.followup.send(
            f"⏭️ Queued #{playlist_num}: {pretty} · "
            f"#{result.get('queue_order', 1)} of {result.get('total_queued', 1)} in queue"
        )

    async def _turn(self, interaction: discord.Interaction, step: int):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message('That picker belongs to someone else.', ephemeral=True)
            return
        self.page = (self.page + step) % self.pages
        start = self.page * GENRE_PAGE_SIZE
        self.results = self.all_results[start:start + GENRE_PAGE_SIZE]
        for child in self.children:
            if isinstance(child, discord.ui.Select):
                child.options = _picker_options(self.results)
        await interaction.response.edit_message(embed=self.build_embed(), view=self)


class RouletteView(GenrePickerView):
    """One random movie from a pool, with the play/queue pickers and a re-spin button."""

    def __init__(self, cog, requester_id: int, genre: Optional[str], pool: List[Tuple[int, dict]]):
        super().__init__(cog, requester_id, genre or "", [random.choice(pool)])
        self.pool = pool
        spin = discord.ui.Button(label='🎲 Spin again', style=discord.ButtonStyle.primary)
        spin.callback = self._spin
        self.add_item(spin)

    async def roulette_embed(self) -> discord.Embed:
        num, item = self.results[0]
        name = item.get('name', '')
        title, year = MediaUtils.parse_movie_filename(name)
        try:
            embed = await asyncio.to_thread(self.cog.tmdb.get_movie_metadata, title, year, item.get('uri'))
        except Exception:
            embed = None
        if not embed:
            pretty = discord.utils.escape_markdown(MediaUtils.clean_filename_for_display(name, max_length=200))
            embed = discord.Embed(title=pretty, color=discord.Color.gold())
        embed.set_author(name=f"🎲 Movie roulette{' · ' + self.genre if self.genre else ''}"[:250])
        embed.set_footer(text=f"#{num} · drawn from {len(self.pool)} movies · play now, queue next, or spin again")
        return embed

    async def _spin(self, interaction: discord.Interaction):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message('That roulette belongs to someone else.', ephemeral=True)
            return
        await interaction.response.defer()  # the TMDB lookup can take a moment
        others = [p for p in self.pool if p[0] != self.results[0][0]] or self.pool
        self.results = [random.choice(others)]
        for child in self.children:
            if isinstance(child, discord.ui.Select):
                child.options = _picker_options(self.results)
        await interaction.edit_original_response(embed=await self.roulette_embed(), view=self)


class PlaylistCommands(commands.Cog):
    # Cap for play-search pickers; the slash embed preview must match this.
    SEARCH_PICKER_CAP = SEARCH_PICKER_CAP

    def __init__(self, bot: commands.Bot, vlc_controller, tmdb_service, watch_service, genre_service=None):
        self.bot = bot
        self.vlc = vlc_controller
        self.tmdb = tmdb_service
        self.watch_service = watch_service
        self.genres = genre_service
        self._genre_index_cache: Tuple[float, dict] = (0.0, {})

    async def cog_load(self):
        if self.genres:
            self._refresh_genres.start()

    async def cog_unload(self):
        self._refresh_genres.cancel()

    @tasks.loop(minutes=2)
    async def _refresh_genres(self):
        """Radarr's lists hourly, plus a few TMDB lookups for movies Radarr doesn't have."""
        try:
            await asyncio.to_thread(self.genres.refresh_radarr)
            items = await asyncio.to_thread(self._get_playlist_items)
            await asyncio.to_thread(self.genres.fill_from_tmdb, items)
            self._genre_index_cache = (0.0, {})  # show what was just learned on the next request
        except Exception:
            # An unhandled error would stop the loop for the rest of the session
            logger.exception("Genre refresh failed")

    @_refresh_genres.before_loop
    async def _before_refresh_genres(self):
        await self.bot.wait_until_ready()

    async def genre_index(self) -> dict:
        """{genre: [(number, item)]} for the current playlist, reused for a minute
        (slash autocomplete asks on every keystroke)."""
        made_at, index = self._genre_index_cache
        if not index or time.monotonic() - made_at > 60:  # an empty one may just be loading
            items = await asyncio.to_thread(self._get_playlist_items)
            index = await asyncio.to_thread(self.genres.by_genre, items)
            self._genre_index_cache = (time.monotonic(), index)
        return index

    async def genre_reply(self, requester_id: int, query: str) -> dict:
        """send() arguments for a genre request: the genre's picker, or the list of genres
        when no genre was given or none matches."""
        index = await self.genre_index()
        if not index:
            return {"content": (
                "No playlist movies have genres yet. They load in the background over the first "
                "few minutes after startup; if this persists, the playlist may be empty or have no movies."
            )}
        genre = match_genre(query, index) if query.strip() else None
        if genre is None:
            shown = discord.utils.escape_markdown(query.strip()[:60])
            text = f"No single genre matches *{shown}*.\n" if query.strip() else ""
            text += "🎭 **Genres in the playlist:** "
            parts = [f"{discord.utils.escape_markdown(g)} ({len(v)})" for g, v in sorted(index.items())]
            # Stay under Discord's 2000-character message limit however many genres there are
            while parts and len(text) + len(" · ".join(parts)) > 1990:
                parts.pop()
            listing = " · ".join(parts)
            if len(parts) < len(index):
                listing += " · …"
            return {"content": text + listing}
        view = GenrePickerView(self, requester_id, genre, index[genre])
        return {"embed": view.build_embed(), "view": view}

    async def roulette_reply(self, requester_id: int, query: str) -> dict:
        """send() arguments for a roulette spin: a random playlist movie (no TV episodes),
        from one genre when given. An unmatched genre gets the genre list instead."""
        if query.strip():
            if not self.genres:
                return {"content": "Genre filtering isn't available."}
            index = await self.genre_index()
            genre = match_genre(query, index) if index else None
            if genre is None:
                return await self.genre_reply(requester_id, query)
            pool = index[genre]
        else:
            genre = None
            items = await asyncio.to_thread(self._get_playlist_items)
            pool = [(n, item) for n, item in enumerate(items, 1) if is_movie(item)]
        if not pool:
            return {"content": "No movies in the playlist to spin."}
        view = RouletteView(self, requester_id, genre, pool)
        return {"embed": await view.roulette_embed(), "view": view}

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

    def _truncate_middle(self, text: str, max_len: int, min_tail: int = 22) -> str:
        """Truncate text from the middle while preserving the end details.

        This helps keep suffix details like `S01E08` visible in long media titles.
        """
        if max_len <= 0:
            return ""
        if len(text) <= max_len:
            return text
        if max_len <= 3:
            return "." * max_len

        # Keep a meaningful suffix (episode/season tags are often near the end).
        tail = min(min_tail, max_len - 3)
        head = max_len - 3 - tail

        # Ensure both sides remain visible even on tighter limits.
        if head < 8:
            head = min(8, max_len - 3)
            tail = max(0, max_len - 3 - head)

        return f"{text[:head]}...{text[-tail:]}"

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
            # Use a longer pre-clean limit for search results so episode suffixes
            # (e.g. S01E08) are not lost before page-level truncation is applied.
            basename = MediaUtils.clean_filename_for_display(name, max_length=240)
            prefix = f"{icon}`{playlist_num}` "
            max_basename_len = max(0, max_chars_per_page - len(prefix))
            display_name = self._truncate_middle(basename, max_basename_len)
            line = f"{prefix}{display_name}"

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

    def make_play_search_view(self, requester_id: int, results: List[Tuple[int, dict]]) -> PlaySearchView:
        """Create a slash-command picker; capping happens inside the view."""
        return PlaySearchView(self, requester_id, results)

    async def _play_and_report(self, ctx, item, playlist_num: int) -> bool:
        """Play an item and reply with its metadata embed. Returns True on success."""
        item_id = item.get('id')
        if not item_id or not self.vlc.play_item(item_id):
            await ctx.send('Error: Could not play the selected item')
            return False

        logger.info(f"Playing search result: {item.get('name')} (#{playlist_num})")
        await ctx.send(f'Loading item #{playlist_num}...')

        search_title, search_year = MediaUtils.parse_movie_filename(item.get('name'))
        movie_embed = self.tmdb.get_movie_metadata(search_title, search_year, file_path=item.get('uri'))
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
        return True

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
            playback_cog = self.bot.get_cog("PlaybackCommands")
            if playback_cog:
                ok, reason = playback_cog._playback_guard(ctx.author)
                if not ok:
                    await ctx.send(reason)
                    return

            results = self._search_items(query)
            if not results:
                await ctx.send('No matches found in playlist')
                return

            if len(results) == 1:
                playlist_num, item = results[0]
                if await self._play_and_report(ctx, item, playlist_num):
                    if playback_cog:
                        playback_cog._seat_playback_owner(ctx.author, item.get('id'))
                return

            # Multiple results: show a numbered list and wait for the user to pick.
            shown = results[:SEARCH_PICKER_CAP]
            lines = []
            for num, item in shown:
                name = item.get('name', '')
                icon = MediaUtils.get_media_icon(name)
                basename = MediaUtils.clean_filename_for_display(name, max_length=60)
                lines.append(f"{icon}`{num}` {basename}")

            # Keep the message under Discord's 2000-char cap: 25 lines of ~70 chars plus this header
            shown_query = query if len(query) <= 60 else query[:59] + "…"
            header = f"**{len(results)} matches** for *{shown_query}* — reply with the number to play, or `cancel` to dismiss."
            if len(results) > len(shown):
                header += f" Showing top {len(shown)}; refine your query for more."
            await ctx.send(header + "\n" + "\n".join(lines))

            valid = {pair[0] for pair in shown}

            def _is_valid_reply(m):
                if m.author != ctx.author or m.channel != ctx.channel:
                    return False
                stripped = m.content.strip().lower()
                return stripped == 'cancel' or (stripped.isdigit() and int(stripped) in valid)

            try:
                picked = await self.bot.wait_for('message', check=_is_valid_reply, timeout=60)
            except asyncio.TimeoutError:
                await ctx.send('Playback selection timed out — run the search again.')
                return

            if picked.content.strip().lower() == 'cancel':
                await ctx.send('Playback selection cancelled.')
                return

            number = int(picked.content.strip())
            _, item = next(p for p in shown if p[0] == number)

            if playback_cog:
                ok, reason = playback_cog._playback_guard(ctx.author)
                if not ok:
                    await ctx.send(reason)
                    return

            if await self._play_and_report(ctx, item, number):
                if playback_cog:
                    playback_cog._seat_playback_owner(ctx.author, item.get('id'))
        except Exception as e:
            logger.error(f"Error in play_search: {e}")
            await ctx.send(f'Error searching and playing: {str(e)}')

    @commands.command(name='genre', aliases=['genres'])
    @commands.has_any_role(*Config.ALLOWED_ROLES)
    async def genre(self, ctx: commands.Context, *, genre: str = ""):
        """List the playlist's movies in a genre and pick one to play"""
        if not self.genres:
            await ctx.send("Genre browsing isn't available.")
            return
        reply = await self.genre_reply(ctx.author.id, genre)
        message = await ctx.send(**reply)
        if "view" in reply:
            reply["view"].message = message

    @commands.command(name='roulette', aliases=['random'])
    @commands.has_any_role(*Config.ALLOWED_ROLES)
    async def roulette(self, ctx: commands.Context, *, genre: str = ""):
        """Pick a random movie from the playlist, optionally from one genre"""
        reply = await self.roulette_reply(ctx.author.id, genre)
        message = await ctx.send(**reply)
        if "view" in reply:
            reply["view"].message = message

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
