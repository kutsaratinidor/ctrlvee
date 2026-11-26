import os
import re
import asyncio
import contextlib
import logging
from typing import List

import discord
from discord.ext import commands

from ..config import Config, get_watch_folders_from_env, parse_watch_folders_value

logger = logging.getLogger(__name__)


def _project_root() -> str:
    """Return the project root directory (where .env typically lives)."""
    # src/cogs/watch.py -> src/cogs -> src -> project root
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, "..", ".."))


def _env_path() -> str:
    """Resolve the .env path; prefer project root, fallback to CWD."""
    root_env = os.path.join(_project_root(), ".env")
    if os.path.exists(root_env):
        return root_env
    cwd_env = os.path.join(os.getcwd(), ".env")
    return cwd_env


def _parse_watch_folders(val: str) -> List[str]:
    """Parse WATCH_FOLDERS env value into normalized absolute paths."""
    return parse_watch_folders_value(val)


def _atomic_write(path: str, content: str) -> None:
    """Write then rename, so the watcher never reads a half-written folder list."""
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp_path, path)


def _write_watch_folders_file(file_path: str, new_list: List[str]) -> bool:
    try:
        lines = [p for p in new_list if p]
        _atomic_write(file_path, "\n".join(lines) + "\n")
        try:
            from dotenv import load_dotenv
            load_dotenv(override=True)
        except Exception:
            pass
        return True
    except Exception as e:
        logger.error(f"Failed to update WATCH_FOLDERS_FILE '{file_path}': {e}")
        return False


def _write_env_watch_folders(new_list: List[str]) -> bool:
    """Update WATCH_FOLDERS in .env with the provided list. Preserves other keys.

    Returns True on success, False otherwise.
    """
    file_path = os.environ.get("WATCH_FOLDERS_FILE", "").strip()
    if file_path:
        return _write_watch_folders_file(file_path, new_list)
    env_file = _env_path()
    try:
        lines: List[str] = []
        if os.path.exists(env_file):
            with open(env_file, "r", encoding="utf-8") as f:
                lines = f.read().splitlines()
        # Build new value (comma + space for readability)
        new_val = ", ".join(new_list)
        key_line = f"WATCH_FOLDERS={new_val}"
        replaced = False
        out_lines: List[str] = []
        for line in lines:
            if line.strip().startswith("#"):
                out_lines.append(line)
                continue
            if re.match(r"^\s*WATCH_FOLDERS\s*=", line):
                out_lines.append(key_line)
                replaced = True
            else:
                out_lines.append(line)
        if not replaced:
            # Ensure file ends with a newline
            if out_lines and out_lines[-1] != "":
                out_lines.append("")
            out_lines.append(key_line)
        _atomic_write(env_file, "\n".join(out_lines) + "\n")
        try:
            # Reload environment so services pick up changes immediately
            from dotenv import load_dotenv
            load_dotenv(override=True)
        except Exception:
            # Non-fatal; watcher also hot-reloads periodically
            pass
        return True
    except Exception as e:
        logger.error(f"Failed to update .env WATCH_FOLDERS: {e}")
        return False


def remove_watch_folder(raw: str, watch_service, vlc, purge_playlist: bool) -> dict:
    """Drop a folder from the watch list config, the running watcher, and (optionally)
    the VLC playlist. Blocking; run via asyncio.to_thread.

    Returns { 'error': str } or { 'folder', 'remaining', 'purge' (dict|None) }.
    """
    norm = os.path.normpath(os.path.abspath(os.path.expanduser(raw.strip())))
    # Hold the watcher's lock from read to sync so no scan (or second removal) interleaves
    with (watch_service.lock if watch_service else contextlib.nullcontext()):
        current_list = get_watch_folders_from_env()
        # Match without requiring the folder to still exist on disk (it may be gone)
        match = next((f for f in current_list if os.path.normcase(f) == os.path.normcase(norm)), None)
        if not match:
            return {'error': f"Folder not in watch list: {norm}"}
        updated = [f for f in current_list if f != match]
        if not _write_env_watch_folders(updated):
            return {'error': "Failed to update watch folders configuration. Please check logs."}
        if watch_service:
            watch_service.sync_folders(updated, forget_removed=True)
    purge = vlc.remove_playlist_items_under(match) if (purge_playlist and vlc) else None
    return {'folder': match, 'remaining': len(updated), 'purge': purge}


def build_watch_remove_embed(result: dict) -> discord.Embed:
    lines = [f"Removed: {result['folder']}", f"Folders remaining: {result['remaining']}"]
    purge = result['purge']
    if purge is not None:
        lines.append(f"Playlist items removed: {purge['removed']}")
        if purge['skipped_current']:
            lines.append("The currently playing item is from this folder and was left in place.")
    return discord.Embed(title="Watch Folder Removed", description="\n".join(lines), color=discord.Color.orange())


class WatchRemoveView(discord.ui.View):
    """Dropdown of the current watch folders; picking one removes it."""

    def __init__(self, author_id: int, folders: List[str], watch_service, vlc, purge_playlist: bool):
        super().__init__(timeout=120)
        self.author_id = author_id
        self.folders = folders
        self.watch_service = watch_service
        self.vlc = vlc
        self.purge_playlist = purge_playlist
        # ponytail: Discord selects cap at 25 options; paginate if anyone watches more folders
        select = discord.ui.Select(
            placeholder="Select a folder to remove",
            options=[
                # Values are list indexes since paths can exceed Discord's 100-char limit
                discord.SelectOption(label=f"{i}. {os.path.basename(f) or f}"[:100], description=f[-100:], value=str(i - 1))
                for i, f in enumerate(folders[:25], start=1)
            ],
        )
        select.callback = self._on_select
        self.add_item(select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Only the person who ran this command can use it.", ephemeral=True)
            return False
        return True

    async def _on_select(self, interaction: discord.Interaction):
        folder = self.folders[int(interaction.data["values"][0])]
        await interaction.response.defer()
        try:
            result = await asyncio.to_thread(remove_watch_folder, folder, self.watch_service, self.vlc, self.purge_playlist)
        except Exception as e:
            logger.error(f"watch remove error: {e}")
            result = {'error': f"Error removing watch folder: {e}"}
        if 'error' in result:
            embed = discord.Embed(title="Watch Folder Not Removed", description=result['error'], color=discord.Color.red())
        else:
            embed = build_watch_remove_embed(result)
        self.stop()
        await interaction.edit_original_response(embed=embed, view=None)


def build_watch_remove_picker(author_id: int, watch_service, vlc, purge_playlist: bool):
    """Return (embed, view) listing the watch folders, or (embed, None) when there are none."""
    folders = get_watch_folders_from_env()
    if not folders:
        return discord.Embed(title="Remove Watch Folder", description="No watch folders are configured."), None
    lines = [f"`{i}.` {f}" for i, f in enumerate(folders, start=1)]
    note = "Its files will also be removed from the playlist." if purge_playlist else "The playlist will be left untouched."
    embed = discord.Embed(
        title="Remove Watch Folder",
        description="\n".join(lines)[:3900] + f"\n\nPick a folder below. {note}",
        color=discord.Color.orange(),
    )
    return embed, WatchRemoveView(author_id, folders, watch_service, vlc, purge_playlist)


class WatchCommands(commands.Cog):
    """Commands to manage watch folders without manually editing .env."""

    def __init__(self, bot: commands.Bot, watch_service=None, vlc=None):
        self.bot = bot
        self.watch_service = watch_service
        self.vlc = vlc

    @commands.command(name="watch_remove")
    @commands.is_owner()
    async def remove_watch_folder_cmd(self, ctx: commands.Context, *, path: str = ""):
        """Remove a folder from the watch list and its files from the playlist.

        Usage: !watch_remove            (pick from a list)
               !watch_remove /absolute/path/to/folder
        Append --keep to leave its files in the playlist.
        """
        keep = path.rstrip().endswith("--keep")
        if keep:
            path = path.rstrip()[:-len("--keep")]
        if not path.strip():
            embed, view = build_watch_remove_picker(ctx.author.id, self.watch_service, self.vlc, not keep)
            await ctx.send(embed=embed, view=view)
            return
        try:
            result = await asyncio.to_thread(remove_watch_folder, path, self.watch_service, self.vlc, not keep)
        except Exception as e:
            logger.error(f"watch_remove error: {e}")
            await ctx.send(f"❌ Error removing watch folder: {e}")
            return
        if 'error' in result:
            await ctx.send(f"❌ {result['error']}")
            return
        await ctx.send(embed=build_watch_remove_embed(result))

    @commands.command(name="watch_add")
    @commands.is_owner()
    async def add_watch_folder(self, ctx: commands.Context, *, path: str):
        """Add a new folder to WATCH_FOLDERS in .env.

        Usage: !watch_add /absolute/path/to/folder
        Accepts ~ (home) and relative paths; they are normalized to absolute.
        """
        try:
            # Normalize and validate
            raw = path.strip()
            norm = os.path.normpath(os.path.abspath(os.path.expanduser(raw)))
            if not os.path.isdir(norm):
                await ctx.send(f"❌ Path not found or not a directory: {raw}")
                return

            # Current list from env
            current_list = get_watch_folders_from_env()

            # Already present?
            if norm in current_list:
                await ctx.send(f"ℹ️ Folder already in watch list: {norm}")
                return

            updated = current_list + [norm]
            if not _write_env_watch_folders(updated):
                await ctx.send("❌ Failed to update .env. Please check logs.")
                return

            # Success message
            embed = discord.Embed(
                title="✅ Watch Folder Added",
                description=(
                    f"Added: {norm}\n\n"
                    f"Total folders: {len(updated)}\n"
                    f"Changes take effect immediately; new files will be discovered on the next scan."
                ),
                color=discord.Color.green(),
            )
            await ctx.send(embed=embed)
        except Exception as e:
            logger.error(f"watch_add error: {e}")
            await ctx.send(f"❌ Error adding watch folder: {e}")
