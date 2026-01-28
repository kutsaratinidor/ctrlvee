## 1.9.3 - 2026-01-27

### Fixed
- **TMDB Metadata Matching**: Significantly improved accuracy of movie and TV show metadata retrieval to resolve incorrect matches for similar titles.
  - **Dual-lookup strategy applied everywhere**: For files without explicit episode markers, the bot now tries both movie and TV series lookups and intelligently selects the better match based on scoring. This fix is now applied to:
    - Playback track changes (now playing)
    - Watch folder announcements
    - Status command lookups
  - This resolves cases where TV shows like "Hell's Paradise" or "Memory of a Killer" were being looked up as movies and returning completely wrong results.
  - Enhanced scoring algorithm: Uses multi-factor scoring (title similarity, year proximity, popularity) instead of simple ranking to better distinguish between similar results.
  - **Original title support**: Now checks both `title` and `original_title` fields, fixing issues with anime and foreign films (e.g., "Hell's Paradise" was incorrectly matched to "My Paradise Is Darker Than Your Hell").
  - **Year-based disambiguation**: Implements year proximity scoring (exact match +50pts, 1-year difference +40pts, etc.) to resolve cases like "Memory of a Killer (2026)" being matched to "The Memory of a Killer (2010)".
  - **TV show year extraction**: `parse_tv_filename()` now extracts year from filenames like "Show.Name.2026.S01E01.mkv" and passes it to TMDB API for better filtering.
  - **Improved logging**: Metadata lookups now log the matched title, original title, year, and confidence score for better debugging.

## 1.9.2 - 2026-01-23

### Fixed
- **Subtitle Selection Tracking**: Subtitle selection is now properly tracked and displayed. Since VLC's HTTP API doesn't expose the currently active subtitle track, the bot now maintains client-side tracking. When you use `!sub_set` to change subtitles, the bot remembers your selection and displays it with ✅ in `!sub_list`.
- **Windows Playlist Cleanup Bug**: Fixed critical bug in `!cleanup` command on Windows that was incorrectly marking all files as missing. The issue was in path URI-to-path conversion where `.lstrip('/')` was removing multiple leading slashes instead of just the first one. Now uses `[1:]` for proper single-slash removal.
- Added comprehensive debug logging to playlist cleanup to help diagnose file path resolution issues on all platforms.

### Changed
- `!sub_list` now displays subtitle selection more clearly with a dedicated "Current" field showing the selected track with ✅ or ⚪ (Off).
- Improved error handling and logging in URI-to-path conversion for cross-platform compatibility.

## 1.9.1 - 2026-01-22

### Fixed
- **SUPPRESS_SINGLE_TV**: Added missing Config class attribute to properly load `SUPPRESS_SINGLE_TV` from environment variables. Previously, the setting was ignored, causing single TV episodes to always be suppressed regardless of the `.env` value.

### Added
- **Watch Folder Management**: New `!watch_add <path>` command to add watch folders directly from Discord without manually editing `.env`. The command validates the directory, normalizes the path, and updates the `.env` file immediately with hot-reload support.

## 1.9.0 - 2025-12-22

### Added
- **Radarr Integration**: New multi-instance Radarr support to view recently downloaded movies from one or more Radarr servers.
  - Single-instance mode: Configure `RADARR_HOST`, `RADARR_PORT`, `RADARR_API_KEY`, `RADARR_USE_SSL` for simple setups.
  - Multi-instance mode: Set `RADARR_INSTANCES` (comma-separated names) and configure each instance with `RADARR_<NAME>_*` environment variables.
  - New command: `!radarr_recent [instance|all] [days] [limit]` - Shows recently added movies in a clean embed with Title (Year) grouped by instance.
  - Examples: `!radarr_recent` (all instances, last 7 days), `!radarr_recent asian 14 15` (specific instance, 14 days, max 15 items).
  - Help integration: Radarr commands appear in `!controls` when configured.
- Config helper method `get_radarr_instances()` to retrieve all configured Radarr instances with display names.

### Changed
- `RadarrService` now safely handles missing config attributes using `getattr()` with fallbacks.
- Updated `template.env` and README with comprehensive Radarr configuration examples.

## 1.8.0 - 2025-12-22

### Changed
- Subtitles UX: `sub_set` now uses GUI order only (no `#`), making selection intuitive in Discord. `sub_list` shows a clean, aligned, monospaced list (e.g., `[x]  2. English`).
- Help text updated: Controls/help and usage strings reflect the simpler `sub_set <number|off>` syntax.

### Fixed
- Subtitle selection reliability: Use VLC stream index where available and filter out the "Disable" pseudo-track so numbering matches what users expect.
- Confirmation message: When VLC doesn't immediately mark selection, show the intended track without noisy suffixes.

### Added
- Extra logging around subtitle parsing/selection to aid troubleshooting (IDs, stream indexes, names).

## 1.7.2 - 2025-12-09

### Changed
- Watch-folder announcements: tightened TV detection for single-file additions. Only treat as TV when an explicit episode token is present (SxxExx or 1x02). Prefer movie metadata when both TV and movie parse but no episode token is found.
- Announcement scheduling: added completion/error callbacks so failures in the async announcement task are logged explicitly.
- Commands: replaced obsolete `_update_now_playing` with unified `_announce_now_playing` to avoid AttributeError.

### Added
- Config toggle `SUPPRESS_SINGLE_TV` (default: true) to control whether single-episode TV additions are suppressed. Set to `false` to announce single episodes immediately.

## 1.7.1 - 2025-12-09

### Changed
- Unified "Now Playing" announcements via a single announcer function to de-duplicate command-initiated and auto monitor events.
- Added cooldowns and a suppression window to prevent redundant messages; values are configurable.
- Refined the admin cleanup command to produce a concise names-only embed with a footer indicating it's not broadcasted.
- Minor robustness tweaks around voice reconnect handling and logging based on config.

## 1.7.0 - 2025-12-09

### Changed
- **Voice Reconnect Hardening & Noise Suppression**: Unified voice connection checks, added debounce windows and an initial settle period to avoid thrashing after reconnects. Suppressed noisy internal `discord.voice_state` and `discord.gateway` logs by adjusting logger levels and handlers.
- **Startup → New Media Ordering**: Ensured the startup announcement is sent before any new media announcements, even when `WATCH_ENQUEUE_ON_START=true`.
- **Initial Scan Simplification**: On first watch-folder scan, announcements are compact lists without TMDB metadata to reduce churn.

### Added
- **Config Toggles for Voice Behavior**: New `.env` keys to tune or disable reconnect behavior and logging:
	- `DISCORD_VOICE_LOG_LEVEL`, `DISCORD_GATEWAY_LOG_LEVEL`
	- `VOICE_INITIAL_SETTLE_SECONDS`, `VOICE_DEBOUNCE_SECONDS`
	- `ENABLE_VOICE_GUARD`, `ENABLE_VOICE_EVENTS_RECONNECT`
- **File Sizes in Announcements**: Batch and single-file announcements now include file size or total batch size.
- **TV Season Batch Embeds**: Multi-episode TV additions produce a season summary and, when possible, TMDB show/season context.

### Fixed
- **TV Title Normalization**: Removed trailing year markers from TV folder names before TMDB queries; improved parsing and fallback logging to avoid empty embeds.
- **Presence Log Spam Reduction**: Deduplicated “presence cleared” logs when playback stops.

## 1.6.1 - 2025-12-04

### Changed
- **Smarter Periodic Announcements**: The periodic announcement system has been refactored to be event-driven. It now only activates when playback starts and deactivates when playback stops. This prevents redundant checks and ensures announcements are only made during active sessions.

## 1.6.0 - 2025-12-03

### Added
- **Periodic "Now Playing" Announcements**: The bot can now periodically announce the currently playing media at a configurable interval. This is useful for users who join mid-playback.
    - Controlled by `PERIODIC_ANNOUNCE_ENABLED` (default: `false`) and `PERIODIC_ANNOUNCE_INTERVAL` (default: `300` seconds) in the `.env` file.
- **Genre in Metadata Embeds**: The "Genre" field is now included in TMDB metadata embeds for `!status` and new file announcements.

### Changed
- **Rich Track Change Announcements**: Automatic announcements for track changes are now rich embeds with TMDB metadata, consistent with `!status`.
- **Configuration-driven Announcements**: The notification system now exclusively uses `WATCH_ANNOUNCE_CHANNEL_ID` from the `.env` file, making it more robust.
- **Announcement De-duplication**: A timestamp-based mechanism prevents duplicate announcements when a track change is triggered by both a user command (e.g., `!next`) and the background monitor.

### Removed
- The `set_notification_channel`, `unset_notification_channel`, and `show_notification_channel` commands have been removed as they are now obsolete.

### Fixed
- The periodic announcement will now wait for the configured interval before sending its first message, preventing a misleading announcement immediately on bot startup.

## 1.5.14 - 2025-11-14

### Fixed
- Fixed a startup race condition where the bot's presence would be cleared before the initial media scan could enqueue files. This occurred when the playlist was empty and `WATCH_ENQUEUE_ON_START` was enabled. A guard has been added to prevent presence clearing until the initial scan is complete.

## 1.5.13 - 2025-11-03

### Added
- Voice reconnect and connection tuning are now configurable via `.env`:
	- `VOICE_MAX_RECONNECTS`, `VOICE_RECONNECT_WINDOW`, `VOICE_RECONNECT_COOLDOWN`
	- `VOICE_CONNECT_TIMEOUT`, `VOICE_CONNECT_RETRY_DELAY`, `VOICE_ERROR_RETRY_DELAY`
	- `VOICE_INITIAL_RETRIES` (controls initial join retry count)

### Other
- Updated template.env to include the new voice configuration options.

## 1.5.12 - 2025-11-02

### Added
- Subtitles: `sub_set <id|off>` to explicitly select a subtitle track by ID or disable subtitles.
- Subtitles: `sub_list` to list available subtitle tracks and show the currently selected one.

### Other
- Bump version to 1.5.12

## 1.5.11 - 2025-11-02

### Added
- Presence progress in activity: the bot can now append playback progress (mm:ss/MM:SS) to the title while VLC is playing or paused.
- Config flags:
	- `ENABLE_PRESENCE_PROGRESS` (default: true) — toggles progress-in-presence updates.
	- `PRESENCE_PROGRESS_UPDATE_INTERVAL` (default: 30) — seconds between progress refreshes.
 - Subtitles control: new commands `sub_next` and `sub_prev` to cycle subtitle tracks via VLC's HTTP interface (when supported).

### Notes
- Progress updates respect presence toggles and run in a lightweight periodic task.
- Title length is trimmed to keep the full presence string within a safe display size.

### Other
- Bump version to 1.5.11

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