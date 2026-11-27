"""Pull the bot's own checkout for the owner-only upgrade command."""
import asyncio
import sys


async def _run(cwd: str, *cmd: str) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    out, _ = await proc.communicate()
    return proc.returncode, out.decode(errors="replace").strip()


def _block(text: str) -> str:
    return f"```\n{text[-1500:]}\n```"


async def pull_update(repo_dir: str) -> tuple[bool, str]:
    """git pull (fast-forward only) and install requirements if they changed.

    Returns (restart, message): restart is True only when new code landed and is ready to run.
    """
    _, before = await _run(repo_dir, "git", "rev-parse", "HEAD")
    code, out = await _run(repo_dir, "git", "pull", "--ff-only")
    if code != 0:
        return False, f"git pull failed, still running the current version:\n{_block(out)}"
    _, after = await _run(repo_dir, "git", "rev-parse", "HEAD")
    if after == before:
        return False, "Already up to date."

    _, log = await _run(repo_dir, "git", "log", "--oneline", "--no-decorate", "-15", f"{before}..{after}")
    _, changed = await _run(repo_dir, "git", "diff", "--name-only", before, after)
    message = f"Pulled:\n{_block(log)}"
    if "requirements.txt" in changed.splitlines():
        code, out = await _run(repo_dir, sys.executable, "-m", "pip", "install", "-q", "-r", "requirements.txt")
        if code != 0:
            # New code may import packages that aren't installed; restarting would crash-loop
            return False, f"{message}\npip install failed, not restarting:\n{_block(out)}"
        message += "\nInstalled updated requirements."
    return True, message
