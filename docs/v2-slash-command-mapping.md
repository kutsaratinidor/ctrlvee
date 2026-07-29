# CtrlVee V2 Slash Command Mapping Matrix

Last updated: 2026-07-29 (implementation slice 2)
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
| cleanup, plcleanup, cleanup_missing | /playback cleanup | none | Open | Planned |
| shuffle_on, shuffle_enable | /playback shuffle-on | none | ALLOWED_ROLES | Planned |
| shuffle_off, shuffle_disable | /playback shuffle-off | none | ALLOWED_ROLES | Planned |
| shuffle_toggle, shuffle | /playback shuffle-toggle | none | ALLOWED_ROLES | Planned |
| sub_list, subs, slist | /subtitles list | none | ALLOWED_ROLES | Planned |
| sub_set, subset, subid | /subtitles set | track_id: str | ALLOWED_ROLES | Planned |
| sub_next, subn, sub+, subnext | /subtitles next | none | ALLOWED_ROLES | Planned |
| sub_prev, subp, sub-, subprev | /subtitles previous | none | ALLOWED_ROLES | Planned |
| audio_list, audios, alist | /audio list | none | ALLOWED_ROLES | Planned |
| audio_set, audioset, audioid | /audio set | track_id: str | ALLOWED_ROLES | Planned |
| list | /playlist list | none | Open | Planned |
| search | /playlist search | query: str (required) | ALLOWED_ROLES | Planned |
| play_search | /playlist play-search | query: str (required) | ALLOWED_ROLES | Planned |
| queue_next, qnext | /queue add-next | number: int | ALLOWED_ROLES | Planned |
| queue_status, qstatus | /queue status | none | ALLOWED_ROLES | Planned |
| clear_queue, qclear | /queue clear | none | ALLOWED_ROLES | Planned |
| remove_queue, qremove, unqueue | /queue remove | ref: str | ALLOWED_ROLES | Planned |
| schedule | /schedule add | number: int, date: str, time: str | Open | Planned |
| schedules | /schedule list | none | Open | Planned |
| unschedule | /schedule remove | number: int | Open | Planned |
| watch_add | /watch add | path: str (required) | ALLOWED_ROLES | Planned |
| controls | /system help | none | Open | Implemented (hybrid) |
| version | /system version | none | Open | Implemented (hybrid) |
| privacy, policy, data_policy | /system privacy | none | Open | Implemented (hybrid) |
| changelog, changes, whatsnew | /system changelog | none | Open | Implemented (hybrid) |
| radarr_recent, recent_movies, recent_radarr | /system radarr-recent | instance: str = 'all', days: int = 7, limit: int = 10 | Open | Implemented (hybrid) |
| leave_server, leave_guild, leave | /admin leave-server | guild_id: int \| None = None | Owner-only | Planned |
| list_guilds, guilds, servers | /admin list-guilds | none | Owner-only | Planned |

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
