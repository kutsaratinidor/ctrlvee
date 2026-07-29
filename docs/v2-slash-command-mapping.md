# CtrlVee V2 Slash Command Mapping Matrix

Last updated: 2026-07-29 (implementation slice 5)
Target branch: feature/v2-slash-commands

## Purpose

This document maps current CtrlVee prefix commands to proposed slash commands for v2 migration. The goal is behavior parity first, then optional consolidation.

## Namespace Proposal

- /playback
- /playlist
- /queue
- /subtitles
- /audio
- /schedule
- /watch
- /system
- /admin

## Mapping Matrix

| V1 command(s) | Proposed v2 slash | Options (current signature) | Current permission | Status |
|---|---|---|---|---|
| play, start, resume | /playback play | none | ALLOWED_ROLES | Implemented (hybrid) |
| pause | /playback pause | none | ALLOWED_ROLES | Implemented (hybrid) |
| stop | /playback stop | none | ALLOWED_ROLES | Implemented (hybrid) |
| restart | /playback restart | none | ALLOWED_ROLES | Implemented (hybrid) |
| next | /playback next | none | ALLOWED_ROLES | Implemented (hybrid) |
| previous | /playback previous | none | ALLOWED_ROLES | Implemented (hybrid) |
| rewind, rw | /playback rewind | seconds: int = 10 | ALLOWED_ROLES | Implemented (hybrid) |
| forward, ff, skip | /playback forward | seconds: int = 10 | ALLOWED_ROLES | Implemented (hybrid) |
| play_num | /playback play-num | number: int | ALLOWED_ROLES | Implemented (hybrid) |
| status, np, nowplaying | /playback status | none | ALLOWED_ROLES | Implemented (hybrid) |
| speed, spd, speed15, speednorm | /playback speed | target: str = None | ALLOWED_ROLES | Planned |
| speedstatus, spdstatus, sr | /playback speed-status | none | ALLOWED_ROLES | Planned |
| cleanup, plcleanup, cleanup_missing | /playback cleanup | none | Owner-only | Implemented (hybrid) |
| shuffle_on, shuffle_enable | /playback shuffle-on | none | ALLOWED_ROLES | Planned |
| shuffle_off, shuffle_disable | /playback shuffle-off | none | ALLOWED_ROLES | Planned |
| shuffle_toggle, shuffle | /playback shuffle-toggle | none | ALLOWED_ROLES | Planned |
| sub_list, subs, slist | /subtitles list | none | ALLOWED_ROLES | Implemented (hybrid) |
| sub_set, subset, subid | /subtitles set | track_id: str | ALLOWED_ROLES | Implemented (hybrid) |
| sub_next, subn, sub+, subnext | /subtitles next | none | ALLOWED_ROLES | Implemented (hybrid) |
| sub_prev, subp, sub-, subprev | /subtitles previous | none | ALLOWED_ROLES | Implemented (hybrid) |
| audio_list, audios, alist | /audio list | none | ALLOWED_ROLES | Implemented (hybrid) |
| audio_set, audioset, audioid | /audio set | track_id: str | ALLOWED_ROLES | Implemented (hybrid) |
| list | /playlist list | none | Open | Implemented (hybrid) |
| search | /playlist search | query: str (required) | ALLOWED_ROLES | Implemented (hybrid) |
| play_search | /playlist play-search | query: str (required) | ALLOWED_ROLES | Implemented (hybrid) |
| queue_next, qnext | /queue add-next | number: int | ALLOWED_ROLES | Implemented (hybrid) |
| queue_status, qstatus | /queue status | none | ALLOWED_ROLES | Implemented (hybrid) |
| clear_queue, qclear | /queue clear | none | ALLOWED_ROLES | Implemented (hybrid) |
| remove_queue, qremove, unqueue | /queue remove | ref: str | ALLOWED_ROLES | Implemented (hybrid) |
| schedule | /schedule add | number: int, date: str, time: str | Open | Implemented (hybrid) |
| schedules | /schedule list | none | Open | Implemented (hybrid) |
| unschedule | /schedule remove | number: int | Open | Implemented (hybrid) |
| watch_add | /watch add | path: str (required) | Owner-only | Implemented (hybrid) |
| controls | /system help | none | Open | Implemented (hybrid) |
| version | /system version | none | Open | Implemented (hybrid) |
| privacy, policy, data_policy | /system privacy | none | Open | Implemented (hybrid) |
| changelog, changes, whatsnew | /system changelog | none | Open | Implemented (hybrid) |
| radarr_recent, recent_movies, recent_radarr | /system radarr-recent | instance: str = 'all', days: int = 7, limit: int = 10 | Open | Implemented (hybrid) |
| leave_server, leave_guild, leave | /admin leave-server | guild_id: int \| None = None | Owner-only | Implemented (hybrid) |
| list_guilds, guilds, servers | /admin list-guilds | none | Owner-only | Implemented (hybrid) |

## Optional Consolidation Decisions (Post-Parity)

- Merge shuffle-on, shuffle-off, shuffle-toggle into one command:
  - /playback shuffle mode:on|off|toggle
- Optionally rename play-num for readability:
  - /playback play-by-number number:<int>
- Optionally move cleanup under /playlist if preferred for taxonomy:
  - /playlist cleanup

## Execution Order

1. /system bootstrap and slash sync flow
2. /playback core controls
3. /playlist and /queue
4. /subtitles and /audio
5. /schedule and /watch
6. /admin
7. parity QA and staged prefix deprecation

## Implemented in Slice 1

- Added slash command sync on startup (`global` or `guild` via `SLASH_COMMAND_GUILD_ID`).
- Added `/system` group commands: `help`, `version`, `privacy`, `changelog`, `radarr-recent`.
- Added migration toggles to support slash-only deployments:
  - `ENABLE_PREFIX_COMMANDS`
  - `ENABLE_SLASH_COMMANDS`
  - `SLASH_COMMAND_GUILD_ID`

## Implemented in Slice 2

- Added `/playback` group commands: `play`, `pause`, `stop`, `restart`, `rewind`, `forward`, `next`, `previous`, `play-num`, `status`.
- Added slash-side role checks that respect `ALLOWED_ROLES` (role names and role IDs).
- Preserved prefix commands in parallel (hybrid mode) for safe rollout.

## Implemented in Slice 3

- Added `/playlist` group commands: `list`, `search`, `play-search`.
- Added `/queue` group commands: `add-next`, `status`, `clear`, `remove`.
- Reused existing playlist search and queue controller logic for behavior parity with prefix commands.
- Preserved prefix commands in parallel (hybrid mode) for safe rollout.

## Implemented in Slice 4

- Added `/subtitles` group commands: `list`, `set`, `next`, `previous`.
- Added `/audio` group commands: `list`, `set`.
- Added owner-only `/playback cleanup` slash command.
- Updated prefix `cleanup` command to owner-only.
- Preserved prefix commands in parallel (hybrid mode) for safe rollout.

## Implemented in Slice 5

- Added `/schedule` group commands: `add`, `list`, `remove`.
- Added `/watch` group command: `add`.
- Added `/admin` owner-only group commands: `list-guilds`, `leave-server`.
- Preserved prefix commands in parallel (hybrid mode) for safe rollout.
