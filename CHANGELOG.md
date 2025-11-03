## 1.5.10 - 2025-11-02

### Added
- Presence logging reasons: all presence updates now include a concise "reason" in logs, e.g., `startup sync`, `track change`, `auto-queue`, `paused at end`, `stopped`, and more.

### Changed
- Presence activity switched to Watching (🎬) to ensure visibility without requiring a streaming URL.
- Presence throttling refined: new titles update immediately; throttling only suppresses identical titles within the configured window.

### Fixed
- Presence now updates on normal playlist transitions (organic track changes, not just queue/commands).
- Presence is cleared when VLC pauses at the end of a track and no queued item exists, preventing stale titles.
- Voice connection reliability: reduced duplicate join attempts and improved reconnect handling for WebSocket codes 4006/4009.

### Dependencies
- Upgraded `discord.py[voice]` to `>=2.4.0,<3.0.0` for improved Python 3.13 stability.
- Relaxed `PyNaCl` to `>=1.5.0` and `requests` to `>=2.31.0`.

### Other
- Bump version to 1.5.10

## 1.5.9 - 2025-11-02

### Added
- Optional voice channel join: the bot can join a specified voice channel on startup (muted and deafened) to make presence visible in the member list. Controlled by:
  - `ENABLE_VOICE_JOIN`: Enable/disable voice channel join (default: true)
  - `VOICE_JOIN_CHANNEL_ID`: Discord voice channel ID to join
  - `VOICE_AUTO_JOIN_ON_START`: Whether to join immediately on startup (default: true)

### Other
- Bump version to 1.5.9

## 1.5.8 - 2025-11-01

### Changed
- Discord presence now uses the Streaming activity with a clapperboard emoji, e.g., "🎬 Title". No streaming URL is required.
- Presence text and behavior clarified; continues to respect `ENABLE_PRESENCE` and `PRESENCE_UPDATE_THROTTLE`.

### Removed
- `STREAMING_URL` configuration option (no longer needed).

### Other
- Bump version to 1.5.8.

## 1.5.7 - 2025-11-01

### Added
- Optional Discord presence updates: the bot can show the currently playing media as its activity (e.g., "Watching <title>"). Controlled by the `.env` flag `ENABLE_PRESENCE` and throttled by `PRESENCE_UPDATE_THROTTLE`.
- `template.env` updated with `ENABLE_PRESENCE` and `PRESENCE_UPDATE_THROTTLE` entries.

### Changed
- `Playback` cog: presence updates implemented and now respect the `ENABLE_PRESENCE` config. Presence is also cleared at startup when presence updates are disabled.

### Other
- Bump version to 1.5.7.

## 1.5.6 - 2025-10-29

### Added
- `src/utils/command_utils.py`: small helper functions `format_cmd` and `format_cmd_inline` to format command usage strings using the configured `DISCORD_COMMAND_PREFIX`.
 - Playback: added `speed` and `speedstatus` commands to control VLC playback rate. `speed` accepts numeric rates (e.g., `1.5`) and presets/aliases (e.g., `normal`, `spd`, `speed15`) and the bot will attempt to reset the rate back to `1.0` when a file finishes.

### Changed
- Refactored cogs to use the new command formatting helpers (`playback`, `playlist`, `scheduler`) so help and inline usage messages always match the configured command prefix.

### Fixed
- Fixed several help-string occurrences and indentation issues discovered while refactoring.

### Other
- Bump version to 1.5.6.

## 1.5.5 - 2025-10-14

### Fixed
- Watch-folder notifier: avoid local 'os' shadowing which could raise UnboundLocalError in some environments when formatting announcement lines. This improves robustness when announcing files from deeply nested folders.

### Other
- Bump version to 1.5.5.

## 1.5.4 - 2025-10-14

### Added
- TMDB: TV/Season metadata lookup for watch-folder season batches. When a season of episodes is added, the bot will attempt to fetch TV/show + season metadata and send a season embed (poster, overview, rating, episode count) after the compact season-summary announcement.

### Changed
- Watch Folders / Announcements: multi-episode season batches now produce a compact "Added Season N" summary embed and, when possible, a TV/season metadata embed to give context about the show.

### Other
- Bump version to 1.5.4.

## 1.5.3

### Changed
- Watch Folders: per-file announcements after the initial scan (instead of grouping) so the bot can fetch and present TMDB metadata per file.
- Watch Folders: added a configurable throttle `WATCH_ANNOUNCE_THROTTLE_MS` (default 500ms) to avoid hammering TMDB/Discord during bursts.
- Playlist Autosave: skip saving when VLC is unreachable or when the playlist has no entries to avoid overwriting the saved playlist with an empty file.

### Other
- Bump version to 1.5.3.

## 1.5.2

### Changed
- Scheduler: Now-playing announcements for scheduled movies include the configured announce role mention (if `WATCH_ANNOUNCE_ROLE_ID` is set).

### Other
- Bump version to 1.5.2.

## 1.5.1

### Changed
- UI: Convert playback control responses to Discord embeds for consistency (play/stop/rewind/forward). Embeds include icons and color cues.

### Other
- Bump version to 1.5.1.

## 1.5.0

### Added
- Optional Playlist Autosave: periodically saves the current VLC playlist to a file.
	- Supports XSPF when `PLAYLIST_AUTOSAVE_FILE` ends with `.xspf` (load directly in VLC).
	- Falls back to JSON when the filename uses a different extension.
	- Controlled via `.env`:
		- `PLAYLIST_AUTOSAVE_FILE` (blank to disable)
		- `PLAYLIST_AUTOSAVE_INTERVAL` (seconds; min 10)

### Changed
- Autosave now prints an info log whenever a playlist is saved (file path and format), for easy tracking.
- Startup announcement can be delayed until the initial watch-folder enqueue is finished when `WATCH_ENQUEUE_ON_START=true`.

### Fixed
- Watch Folders: improved hot-reload parsing and normalization (comma or semicolon separators, trims quotes, absolute normpaths).
- Watch Folders: prevent re-enqueueing previously discovered files when the order of `WATCH_FOLDERS` changes by marking files as seen on the first pass even if not enqueued.

### Other
- Bump version to 1.5.0.

## 1.4.1

### Improved
- Watch Folder Service performance: replaced per-file sleep-based stability checks with non-blocking, cross-scan tracking. This removes a 2s sleep per file and greatly speeds up large imports.
- Incremental media size caching: adds sizes of newly enqueued files instead of rescanning the entire library every cycle.
- New config `WATCH_STABLE_AGE` (seconds) controls how long a file must remain unchanged to be considered stable (default 2s).

### Other
- Bump version to 1.4.1.

## 1.4.0

### Added
- Command: `remove_queue` (aliases: `qremove`, `unqueue`) to remove a queued entry either by queue order (e.g., `1`) or by playlist number (e.g., `#10`).
- Watch-folder announcements now include TMDB metadata when exactly one item is added (if TMDB API key is configured).

### Other
- Bump version to 1.4.0 for these additions.

## 1.3.0

### Added
- **10-Minute Pre-Announcement:** The bot now sends a reminder to all announce channels 10 minutes before a scheduled movie starts.
- **Optional Role Mention:** You can set `WATCH_ANNOUNCE_ROLE_ID` in `.env` to mention a specific role in the pre-announcement. If unset or 0, no role is mentioned.

### Fixed
- **Scheduler Robustness:** Added a safeguard to always initialize the pre-announcement tracking set, preventing rare attribute errors.

### Other
- Bump version to 1.3.0 for these features and fixes.
## 1.2.0

### Added
- **Embedded Startup Message:** On startup, the bot now sends an embedded "CtrlVee Bot is Online!" message to all configured announcement channels, showing the version and command prefix.

### Improved
- **Media Duration Handling:** Improved cross-platform extraction of media duration from VLC playlist items, with robust fallback for Windows and missing/invalid data.

### Other
- Bump version to 1.2.0 for these enhancements.
## 1.1.1

### Fixed
- **Cross-Platform Timezone Handling**: The bot now correctly handles the "Asia/Manila" timezone on both Windows and Unix systems. On Windows, it falls back to "Singapore Standard Time" or the system local timezone if needed. This prevents errors when scheduling movies in PH time on Windows.

### Other
- Bump version to 1.1.1 for this fix.
## 1.1.0

### Added
- **Configurable Command Prefix**: You can now set the bot's command prefix using the `DISCORD_COMMAND_PREFIX` variable in your `.env` file. Supports any string, including multi-character prefixes (e.g., `!`, `!!`, `$`).

### Changed
- The command prefix is now shown in the config printout and documented in the README and template.env.

### Migration Notes
- Add `DISCORD_COMMAND_PREFIX=!!` (or your preferred prefix) to your `.env` to change the prefix. If not set, the default is `!`.

### Other
- Bump version to 1.1.0 for this feature.

## 1.0.0

### Added
- **Multiple Announce Channel Support**: Announcements can now be sent to multiple Discord channels. Configure `WATCH_ANNOUNCE_CHANNEL_ID` in `.env` as a comma-separated list of channel IDs.
- **Dynamic Config Loading**: Announcement channel IDs are now always loaded dynamically from `.env` at runtime.
- **Startup Diagnostics**: The bot prints the resolved announce channel IDs as a list on startup for easier debugging.

### Changed
- Refactored config: `WATCH_ANNOUNCE_CHANNEL_ID` is no longer a static class variable, but is accessed via a static method for live reloads.
- Improved logging for channel resolution and announcement delivery.

### Breaking Changes
- The config variable for announce channels must now be set as `WATCH_ANNOUNCE_CHANNEL_ID` (singular, not plural) in `.env`.
- Code and cogs that accessed `Config.WATCH_ANNOUNCE_CHANNEL_ID` must now use `Config.get_announce_channel_ids()`.

### Migration Notes
- Update your `.env` to use `WATCH_ANNOUNCE_CHANNEL_ID=123,456,...` (comma-separated, no spaces).
- Update any custom code to use the new static method for channel IDs.

### Other
- Bump version to 1.0.0 for breaking changes and new features.

## 0.3.0

### Added
- Media library size is now displayed in `!status` and `!list` commands (shows total size of all watched folders).
- Hot reloading of watch folders: add new folders to `.env` and they are picked up live, no restart needed.
- Log progress indicator (N/total) for each file enqueued from watch folders.

### Changed
- Major performance improvement: media size is now cached and updated after each scan, making commands instant even for large libraries.
- Only log new folders and progress, not every .env reload.

## 0.2.0

### Added
- Optional Watch Folders service: poll configured directories and auto-enqueue new media to VLC.
- Config keys: WATCH_FOLDERS, WATCH_SCAN_INTERVAL.

### Changed
- Bump version to 0.2.0.

# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog and this project adheres to Semantic Versioning.

## [0.1.0] - 2025-08-11
### Added
- !forward (!ff) command to fast forward by N seconds (default 10).
- !version command to display bot version and key config.
- Pagination now respects ITEMS_PER_PAGE from .env.

### Changed
- Filename parsing and display cleaning remove HC/hardsub markers and more torrent noise; preserve numeric titles.

### Docs
- README updated with new commands and versioning instructions.
