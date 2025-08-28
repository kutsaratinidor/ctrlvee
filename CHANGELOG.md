## 0.4.0

### Added
- **Scheduling Feature**: Users can now schedule movies by playlist number and PH time using `!schedule <number> <YYYY-MM-DD> <HH:MM>`. Includes:
	- Conflict detection (prevents double-booking the same movie at the same time)
	- Duration display in schedule and confirmation
	- Schedule persistence across restarts
	- Metadata embed from TMDB when a scheduled movie is played
	- All schedule confirmations and listings are now Discord embeds
- `!schedules` to list all upcoming scheduled movies
- `!unschedule <number>` to remove all schedules for a movie

### Changed
- Bump version to 0.4.0

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
