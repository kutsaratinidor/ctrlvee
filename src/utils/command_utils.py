from typing import Callable
from ..config import Config


def format_cmd(command: str) -> str:
    """Format a command usage string using the configured command prefix.

    Example:
        format_cmd('play_num <n>') -> '!play_num <n>' (if prefix is '!')

    The caller should not include the prefix in `command`.
    """
    prefix = Config.DISCORD_COMMAND_PREFIX or '!'
    return f"{prefix}{command}"


def format_cmd_inline(command: str) -> str:
    """Return the formatted command wrapped in backticks for inline display.

    Example:
        format_cmd_inline('play_num 1') -> '`!play_num 1'`
    """
    return f"`{format_cmd(command)}`"
