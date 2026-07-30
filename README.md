# CtrlVee

A Discord bot that controls a local VLC player and exposes playback controls, playlist search, queueing, scheduling, watch-folder ingestion, and metadata lookups.

Current app version: `1.9.26`.

## What It Does

- Controls VLC playback from Discord (`play`, `pause`, `next`, `rewind`, speed, shuffle, etc.)
- Lets users browse/search the active VLC playlist with paginated embeds
- Supports a soft queue system that works with VLC shuffle behavior
- Schedules playlist items using Philippines time (`Asia/Manila`)
- Watches folders for new media and enqueues files automatically
- Enriches now-playing/status messages with TMDB movie/TV metadata
- Optionally shows recent downloads from one or more Radarr instances
- Optionally auto-joins a voice channel so the bot stays visibly present

## Requirements

- Python `3.10+`
- VLC Media Player with HTTP interface enabled
- A Discord bot token
- TMDB API key (recommended; required for full metadata features)

## Quick Setup

1. Clone this repository.
2. Create and activate a virtual environment.
3. Install dependencies.
4. Copy `template.env` to `.env` and configure values.
5. Start the bot.

```bash
# 1) create venv
python3 -m venv .venv

# 2) activate
# macOS/Linux
source .venv/bin/activate
# Windows PowerShell
# .\.venv\Scripts\Activate.ps1

# 3) install deps
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

# 4) configure env
cp template.env .env

# 5) run
python bot.py
```

## VLC Setup

Enable VLC's web interface before running the bot:

1. Open VLC settings.
2. Go to main interface options.
3. Enable `Web` interface.
4. Set password (if desired) and restart VLC.

Use `.env` values for connection:
- `VLC_HOST` (default `localhost`)
- `VLC_PORT` (default `8080`)
- `VLC_PASSWORD` (default `vlc`)

## Discord Setup

In Discord Developer Portal, ensure your bot has:

- `MESSAGE CONTENT INTENT` enabled
- Permissions to read/send messages and embeds in the channels you use

For larger bots (including bots approaching or exceeding 10k servers), Discord may require stronger justification and transparency for privileged intents.
CtrlVee requests Message Content Intent strictly for prefix command parsing, and includes an in-bot privacy statement via `!privacy`.

### Slash Migration Note

CtrlVee now supports hybrid prefix+slash operation during v2 migration.

- `ENABLE_PREFIX_COMMANDS=true|false`
- `ENABLE_SLASH_COMMANDS=true|false`
- `SLASH_COMMAND_GUILD_ID=<guild_id_or_0>`
- `SYNC_GLOBAL_COMMANDS=true|false` (recommended `false` when `SLASH_COMMAND_GUILD_ID` is set)

If you set `ENABLE_PREFIX_COMMANDS=false` and keep slash commands enabled, the bot can run without Message Content Intent dependency for command parsing.

## Configuration

Edit `.env` (starting from `template.env`).

### Required or Strongly Recommended

- `DISCORD_TOKEN`: Discord bot token (required)
- `ALLOWED_ROLES`: Roles that can run protected commands; supports role names, role IDs, and role mentions
- `TMDB_API_KEY`: Recommended for metadata embeds and lookups

### Core Behavior

- `DISCORD_COMMAND_PREFIX` (default `!`)
- `ENABLE_PREFIX_COMMANDS` (default `true`)
- `ENABLE_SLASH_COMMANDS` (default `true`)
- `SLASH_COMMAND_GUILD_ID` (default `0`, global sync)
- `SYNC_GLOBAL_COMMANDS` (default `false`; avoids duplicate guild+global entries in dev guild mode)
- `ITEMS_PER_PAGE` (default `20`)
- `QUEUE_BACKUP_FILE` (default `queue_backup.json`)
- `PLAYLIST_AUTOSAVE_FILE` + `PLAYLIST_AUTOSAVE_INTERVAL` (optional autosave)
- `KOFI_URL` (optional support link in embeds)
- `PRIVACY_POLICY_URL` (optional public privacy-policy URL shown by `!privacy` and `!version`)
- `PRIVACY_CONTACT` (optional contact for privacy/data requests)

### Watch Folders

- `WATCH_FOLDERS`: comma/semicolon-separated absolute paths
- `WATCH_FOLDERS_FILE`: optional file containing one path per line (takes precedence over `WATCH_FOLDERS`)
- `WATCH_SCAN_INTERVAL` (default `10`)
- `WATCH_STABLE_AGE` (default `2`)
- `WATCH_ENQUEUE_ON_START` (default `true`)
- `WATCH_ANNOUNCE_CHANNEL_ID`: comma-separated channel IDs
- `WATCH_ANNOUNCE_ROLE_ID`: optional role mention ID for schedule announcements
- `WATCH_ANNOUNCE_MAX_ITEMS`
- `WATCH_ANNOUNCE_THROTTLE_MS`
- `SUPPRESS_SINGLE_TV`

### Presence and Periodic Announcements

- `ENABLE_PRESENCE`
- `PRESENCE_UPDATE_THROTTLE`
- `ENABLE_PRESENCE_PROGRESS`
- `PRESENCE_PROGRESS_UPDATE_INTERVAL`
- `PERIODIC_ANNOUNCE_ENABLED`
- `PERIODIC_ANNOUNCE_INTERVAL`

### Voice Auto-Join / Reconnect

- `ENABLE_VOICE_JOIN`
- `VOICE_JOIN_CHANNEL_ID`
- `VOICE_AUTO_JOIN_ON_START`
- `VOICE_MAX_RECONNECTS`
- `VOICE_RECONNECT_WINDOW`
- `VOICE_RECONNECT_COOLDOWN`
- `VOICE_CONNECT_TIMEOUT`
- `VOICE_CONNECT_RETRY_DELAY`
- `VOICE_ERROR_RETRY_DELAY`
- `VOICE_INITIAL_RETRIES`
- `DISCORD_VOICE_LOG_LEVEL`
- `VOICE_INITIAL_SETTLE_SECONDS`
- `VOICE_DEBOUNCE_SECONDS`
- `ENABLE_VOICE_GUARD`
- `ENABLE_VOICE_EVENTS_RECONNECT`

### Radarr (Optional)

Single instance:

```bash
RADARR_HOST=localhost
RADARR_PORT=7878
RADARR_API_KEY=your_key
RADARR_USE_SSL=false
```

Multi-instance:

```bash
RADARR_INSTANCES=main,anime

RADARR_MAIN_HOST=localhost
RADARR_MAIN_PORT=7878
RADARR_MAIN_API_KEY=your_main_key
RADARR_MAIN_USE_SSL=false
RADARR_MAIN_DISPLAY_NAME=Main Movies

RADARR_ANIME_HOST=radarr-anime.local
RADARR_ANIME_PORT=7878
RADARR_ANIME_API_KEY=your_anime_key
RADARR_ANIME_USE_SSL=true
RADARR_ANIME_DISPLAY_NAME=Anime
```

## Commands

Prefix shown as `!` below; replace with your configured `DISCORD_COMMAND_PREFIX`.

### Playback

- `!play`, `!pause`, `!stop`, `!restart`
- `!next`, `!previous`
- `!rewind [seconds]`, `!forward [seconds]`
- `!play_num <number>`
- `!shuffle`, `!shuffle_on`, `!shuffle_off`
- `!speed <rate|preset>`
- `!speedstatus`

### Playlist

- `!list`
- `!search <query>`
- `!play_search <query>`
- `!cleanup` (aliases: `plcleanup`, `cleanup_missing`) removes missing files from VLC playlist

### Subtitles and Audio

- `!sub_list`
- `!sub_set <number|off>`
- `!sub_next`, `!sub_prev`
- `!audio_list`
- `!audio_set <number>`

### Queue

- `!queue_next <number>`
- `!queue_status`
- `!clear_queue`
- `!remove_queue <N|#N>`

### Scheduling

- `!schedule <number> <YYYY-MM-DD> <HH:MM>`
- `!schedules`
- `!unschedule <number>`

### Info / Utility

- `!status`
- `!version`
- `!privacy` (aliases: `policy`, `data_policy`)
- `!changelog` (aliases: `changes`, `whatsnew`)
- `!controls`

## Privacy Statement

CtrlVee includes a built-in privacy statement command: `!privacy`.

Canonical policy file in this repo: [PRIVACY.md](PRIVACY.md).

## Terms of Service

Canonical terms file in this repo: [TERMS.md](TERMS.md).

Use this URL as your public terms link when submitting Discord verification details.

The statement explains:
- what Discord data is processed (including Message Content Intent for command parsing),
- what data may be stored locally (queue/schedule backups, optional autosave, logs),
- what data is not used for (no sale, no ad targeting).

For public deployments, set these in `.env`:
- `PRIVACY_POLICY_URL` (link to your full policy)
- `PRIVACY_CONTACT` (email/support URL/server)

### Upgrade Notes (1.9.15+)

- Add optional privacy metadata to `.env`:
	- `PRIVACY_POLICY_URL=https://your-domain/privacy`
	- `PRIVACY_CONTACT=your-email-or-support-link`
- Run `!privacy` after deploy to verify the in-bot statement renders as expected.
- Update your Discord Developer Portal app description/about text to include your privacy policy URL for easier reviewer/user access.

### Watch Folders

- `!watch_add <path>`

### Radarr

- `!radarr_recent [instance|all] [days] [limit]`

### Owner-Only

- `!list_guilds` (aliases: `guilds`, `servers`)
- `!leave_server [guild_id]` (aliases: `leave_guild`, `leave`)

## Notes

- Most commands are role-gated via `ALLOWED_ROLES`.
- Commands `set_notification_channel`, `unset_notification_channel`, and `show_notification_channel` are obsolete and not part of the current bot.
- `WATCH_FOLDERS_FILE` overrides `WATCH_FOLDERS` when set.
- Playlist autosave writes XSPF when the target filename ends with `.xspf`; otherwise JSON is written.

## Changelog and Releases

- Detailed history is in `CHANGELOG.md`.
- Version is defined in `src/version.py` and shown with `!version`.

## License

MIT. See `LICENSE`.
