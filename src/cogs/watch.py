import os
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


def _write_watch_folders_file(file_path: str, new_list: List[str]) -> bool:
    try:
        lines = [p for p in new_list if p]
        with open(file_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
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
        with open(env_file, "w", encoding="utf-8") as f:
            f.write("\n".join(out_lines) + "\n")
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


class WatchCommands(commands.Cog):
    """Commands to manage watch folders without manually editing .env."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="watch_add")
    @commands.has_any_role(*Config.ALLOWED_ROLES)
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
