# CtrlVee Privacy Statement

Last updated: 2026-06-19

This document describes what data CtrlVee processes, why it is processed, and how it is used.

## Scope

CtrlVee is a Discord bot that controls a local VLC instance and optional metadata integrations. It is designed to process only the data needed to provide bot features.

## Data We Process

CtrlVee may process:

- Message content for command parsing (Message Content Intent)
- Basic Discord metadata required to execute commands and permissions checks
  - user IDs
  - guild IDs
  - channel IDs
  - role IDs / role names
- Voice state and channel information when voice auto-join/reconnect features are enabled

## Data We Store

Depending on configuration, CtrlVee may store data locally in files and logs, including:

- Queue backups (for queue continuity)
- Schedule backups (for scheduled playback continuity)
- Optional playlist autosave output
- Operational logs for troubleshooting and bot maintenance

CtrlVee is self-hosted and stores this data in the host environment under your deployment control.

## Data Use

Processed/stored data is used only for:

- Bot command handling
- Playback control features
- Scheduling and queue features
- Stability/diagnostics

CtrlVee does not use data for ad targeting and does not sell personal data.

## Third-Party Services

If enabled by configuration, CtrlVee may call external APIs/services (for example TMDB or Radarr) for metadata and media-management features. These integrations are optional and controlled by your environment settings.

## Retention and Deletion

Retention is controlled by your deployment setup:

- Remove local backup/log files to clear stored operational data.
- Disable optional features in environment settings to reduce future data processing/storage.
- Remove the bot from a server to stop server-side command processing for that server.

## Contact

Set your deployment contact in environment config:

- PRIVACY_CONTACT

Set your public policy URL in environment config:

- PRIVACY_POLICY_URL

The in-bot command !privacy surfaces this policy summary to end users.
