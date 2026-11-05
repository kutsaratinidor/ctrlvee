"""Interactive wizard that writes .env from template.env for the essential settings.

Run standalone:
    python scripts/configure_env.py

Or imported and called from setup.py's run_wizard(repo_root).
"""
import getpass
from pathlib import Path


def prompt(question: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default is not None else ""
    while True:
        answer = input(f"{question}{suffix}: ").strip()
        if answer:
            return answer
        if default is not None:
            return default
        print("This value is required.")


def prompt_port(question: str, default: str) -> str:
    while True:
        value = prompt(question, default=default)
        if value.isdigit():
            return value
        print("Enter a numeric port.")


def prompt_token() -> str:
    while True:
        token = getpass.getpass("Discord bot token (input hidden): ").strip()
        if token:
            return token
        print("A Discord bot token is required.")


def prompt_choice(question: str, choices: list[str], default_index: int) -> int:
    print(question)
    for i, choice in enumerate(choices, start=1):
        marker = " (default)" if i - 1 == default_index else ""
        print(f"  {i}) {choice}{marker}")
    while True:
        answer = input(f"Choose [1-{len(choices)}] [{default_index + 1}]: ").strip()
        if not answer:
            return default_index
        if answer.isdigit() and 1 <= int(answer) <= len(choices):
            return int(answer) - 1
        print(f"Enter a number from 1 to {len(choices)}.")


def patch_env_lines(template_lines: list[str], values: dict[str, str]) -> list[str]:
    remaining = dict(values)
    result = []
    for line in template_lines:
        stripped = line.rstrip("\n")
        if "=" in stripped and not stripped.lstrip().startswith("#"):
            key = stripped.split("=", 1)[0]
            if key in remaining:
                result.append(f"{key}={remaining.pop(key)}\n")
                continue
        result.append(line)
    if remaining:
        raise KeyError(f"Keys not found in template.env: {sorted(remaining)}")
    return result


def write_env_file(repo_root: Path, lines: list[str]) -> Path:
    env_path = repo_root / ".env"
    tmp_path = repo_root / ".env.tmp"
    tmp_path.write_text("".join(lines), encoding="utf-8")
    tmp_path.replace(env_path)
    return env_path


def run_wizard(repo_root: Path) -> Path:
    template_path = repo_root / "template.env"
    template_lines = template_path.read_text(encoding="utf-8").splitlines(keepends=True)

    print("\n--- CtrlVee setup: essential configuration ---")
    print("Press Enter to accept the default in [brackets] where shown.\n")

    print("Get a Discord bot token at: https://discord.com/developers/applications")
    print("(create an application, add a Bot, then copy the token from the Bot tab)")
    token = prompt_token()
    roles = prompt("Allowed roles (comma-separated names or IDs)", default="Theater Host")

    mode_index = prompt_choice(
        "Command mode:",
        ["Prefix only", "Slash only", "Both"],
        default_index=0,
    )
    enable_prefix = mode_index in (0, 2)
    enable_slash = mode_index in (1, 2)

    values = {
        "DISCORD_TOKEN": token,
        "ALLOWED_ROLES": roles,
        "ENABLE_PREFIX_COMMANDS": "true" if enable_prefix else "false",
        "ENABLE_SLASH_COMMANDS": "true" if enable_slash else "false",
        # The wizard doesn't collect a voice channel, and CtrlVee's config
        # validation refuses to start with voice auto-join on but no channel
        # configured — so turn it off here rather than hand back a .env the
        # bot can't boot with. Users who want it can re-enable it in .env.
        "ENABLE_VOICE_JOIN": "false",
    }

    if enable_prefix:
        values["DISCORD_COMMAND_PREFIX"] = prompt("Command prefix character", default="!")

    values["VLC_HOST"] = prompt("VLC host", default="localhost")
    values["VLC_PORT"] = prompt_port("VLC port", default="8080")
    values["VLC_PASSWORD"] = prompt("VLC web interface password", default="vlc")

    # Required, not optional: CtrlVee's config validation refuses to start
    # without a TMDB_API_KEY, so a blank/placeholder value here would hand
    # back a .env the bot can't boot with.
    print("\nGet a free TMDB API key at: https://www.themoviedb.org/settings/api")
    values["TMDB_API_KEY"] = prompt("TMDB API key (required)", default=None)

    patched_lines = patch_env_lines(template_lines, values)
    env_path = write_env_file(repo_root, patched_lines)

    print(f"\nWrote {env_path}")
    print("Voice auto-join was disabled (no channel configured) — to enable it,")
    print("set ENABLE_VOICE_JOIN=true and VOICE_JOIN_CHANNEL_ID in .env.")
    print("Everything else (watch folders, Radarr, presence, etc.) was left at")
    print("template.env's defaults — see README.md to configure those.\n")
    return env_path


if __name__ == "__main__":
    run_wizard(Path(__file__).resolve().parent.parent)
