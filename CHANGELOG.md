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
