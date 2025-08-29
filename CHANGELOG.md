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
