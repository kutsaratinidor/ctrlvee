# Movie Request Integration (Overseerr/Jellyseerr) — Design

## Summary

Add a Discord slash command that lets allowed users request a movie, which the bot
submits to a self-hosted Overseerr/Jellyseerr ("Seerr") instance. The bot tracks who
requested what, polls Seerr for availability, and posts an announcement (with a
mention of the requester) in a configurable channel once the movie is available.

## Goals

- `/request movie <title>` restricted to a configurable channel (`REQUEST_CHANNEL_ID`)
  and gated by the existing `ALLOWED_ROLES` mechanism.
- Search-then-pick UX: bot searches TMDB, shows candidates, user picks the correct
  one before anything is submitted to Seerr.
- Local tracking of request → Discord requester, independent of Seerr's own user
  model (the bot submits under its own Seerr API key; no per-Discord-user Seerr
  account mapping).
- Background polling (no inbound webhook/HTTP server) detects when a tracked
  request becomes available on Seerr.
- On availability, post an embed with the movie's metadata to a configurable
  announce channel and `@mention` the original requester.

## Non-goals

- No TV show requests (movies only, matching the rest of the bot's TMDB/Radarr
  scope).
- No Seerr webhook receiver / inbound HTTP server.
- No per-Discord-user Seerr accounts or permission delegation in Seerr.
- No prefix-command (`!`) equivalent — slash-only, per project discussion (new
  functionality, not parity work on an existing prefix command).
- No de-duplication/merging of multiple Discord users requesting the same movie;
  Seerr's own duplicate-request error is surfaced as-is.

## Architecture

Two new modules, matching existing patterns in this codebase:

### `src/services/overseerr_service.py` — `OverseerrService`

Thin HTTP API client, same shape as `RadarrService`:

- `__init__(base_url=None, api_key=None)` — falls back to `Config.OVERSEERR_URL` /
  `Config.OVERSEERR_API_KEY` when not passed explicitly (same optional-Config-fallback
  pattern `RadarrService.__init__` uses).
- `is_configured() -> bool`
- `async test_connection() -> dict` — `{"success": bool, "message"|"error": str}`
- `async request_movie(tmdb_id: int) -> dict` — `POST /api/v1/request` with
  `{"mediaType": "movie", "mediaId": tmdb_id}`. Returns
  `{"success": True, "request_id":, "media_id":}` or
  `{"success": False, "error": str}` (Seerr's error message passed through, e.g.
  already requested/available).
- `async get_movie_status(tmdb_id: int) -> dict` — `GET /api/v1/movie/{tmdb_id}`.
  Returns `{"success": True, "status": <int>, "available": bool}` where `available`
  is `status == 5` (Seerr's `MediaStatus.AVAILABLE`). `{"success": False, "error": str}`
  on failure (network error, 404, etc.) — the poller treats this as "skip this cycle,"
  never as "not available yet" being persisted as a state change.

All HTTP calls use `requests` via `loop.run_in_executor`, matching `RadarrService`.

### `src/services/movie_request_tracker.py` — `MovieRequestTracker`

Owns local state and the poll loop, same shape as `WatchFolderService`:

- Constructed with an `OverseerrService` instance.
- JSON persistence to `Config.REQUEST_STORE_FILE` (default `movie_requests.json`),
  loaded on init, written after every mutation — same load/save-on-write approach as
  `vlc_controller`'s queue backup.
- `add_request(record: dict)` — appends a new tracked record and persists.
- `start()` / `stop()` — spawns/stops a daemon polling thread
  (`threading.Thread(..., daemon=True)`), same as `WatchFolderService.start()`.
- `set_notifier(callback)` — callback invoked (from the polling thread) with the
  full record dict when a request transitions to available. bot.py wires this with
  `asyncio.run_coroutine_threadsafe(...)` exactly as it does today for
  `watch_service.set_notifier`.
- Poll loop: every `Config.REQUEST_POLL_INTERVAL` seconds, for every record with
  `status != "available"`, calls `overseerr_service.get_movie_status(tmdb_id)`. On
  `available: True`, sets `status = "available"`, `notified_at = <iso timestamp>`,
  invokes the notifier, and persists. Records are never deleted — they remain as a
  local history log (pending and available).

### `tmdb_service.py` addition

New public method `search_movies(title: str, limit: int = 5) -> list[dict]`, each
item: `{tmdb_id, title, year, overview, poster_path}`. Uses the same
`tmdb.Search()` call already used internally by `_find_best_movie_result`, but
returns the top N raw candidates instead of collapsing to a single best match. No
change to existing methods/behavior.

### `bot.py` additions

- New `request_group = app_commands.Group(name="request", description="CtrlVee media requests")`,
  registered in `_register_app_command_groups()` alongside the other groups.
- `/request movie <title>` handler:
  1. If `Config.REQUEST_CHANNEL_ID` is unset, or `OverseerrService.is_configured()`
     is false → ephemeral reply: "Movie requests are not configured on this bot."
  2. If `interaction.channel.id != Config.REQUEST_CHANNEL_ID` → ephemeral reply
     pointing at the configured channel.
  3. `await _check_allowed_roles_for_interaction(interaction)` (existing helper).
  4. `interaction.response.defer(thinking=True)`.
  5. `tmdb_service.search_movies(title)`. Empty → followup "No results found for
     '{title}'."
  6. Show a `discord.ui.View` with a `discord.ui.Select` (one option per candidate:
     `"{title} ({year})"`), 60s timeout. On timeout, edit the message to say
     selection timed out and disable the select.
  7. On selection: `overseerr_service.request_movie(tmdb_id)`.
     - Success: build a confirmation embed with poster/title/year/overview, call
       `movie_request_tracker.add_request({..., "requested_by_id": interaction.user.id,
       "requested_by_name": str(interaction.user), "status": "pending",
       "requested_at": <iso>})`, edit the message with the embed.
     - Failure: edit the message with Seerr's error text.
- New notifier function wired at startup (mirroring `watch_service`'s notifier
  wiring): builds an embed from the record's stored metadata (poster/title/
  year/overview) and posts it to `Config.REQUEST_ANNOUNCE_CHANNEL_ID` (falls back
  to `REQUEST_CHANNEL_ID` if unset) with `f"<@{record['requested_by_id']}> your
  requested movie is now available!"` as the message content, embed attached.
- `movie_request_tracker.start()` called alongside `watch_service.start()` in the
  existing startup sequence; instance constructed at module level near the other
  service instances.

## Data model (`movie_requests.json`)

```json
[
  {
    "tmdb_id": 603,
    "title": "The Matrix",
    "year": 1999,
    "overview": "...",
    "poster_path": "/....jpg",
    "overseerr_request_id": 42,
    "overseerr_media_id": 17,
    "requested_by_id": 123456789012345678,
    "requested_by_name": "someuser",
    "requested_at": "2026-08-28T12:00:00+00:00",
    "status": "pending",
    "notified_at": null
  }
]
```

## Configuration (`template.env` + `Config`)

| Variable | Default | Notes |
|---|---|---|
| `OVERSEERR_URL` | `''` | Base URL of the Seerr instance, e.g. `http://localhost:5055` |
| `OVERSEERR_API_KEY` | `''` | Seerr API key |
| `REQUEST_CHANNEL_ID` | `0` | Channel where `/request movie` is allowed; `0` disables the feature |
| `REQUEST_ANNOUNCE_CHANNEL_ID` | `0` | Where availability announcements post; `0` falls back to `REQUEST_CHANNEL_ID` |
| `REQUEST_POLL_INTERVAL` | `900` | Seconds between availability checks |
| `REQUEST_STORE_FILE` | `movie_requests.json` | JSON persistence path, relative to bot dir if not absolute |

`Config.validate()` additions, matching the existing Radarr validation style (warn,
don't hard-fail the whole app):

- If exactly one of `OVERSEERR_URL` / `OVERSEERR_API_KEY` is set → error ("Seerr is
  partially configured; set both or clear both").
- If both Seerr vars are set but `REQUEST_CHANNEL_ID` is `0` → error (Seerr
  configured but no channel to operate the feature in).
- `REQUEST_POLL_INTERVAL < 60` → error (avoid hammering a self-hosted instance).

## Error handling

- Seerr unreachable/misconfigured: command replies with the connection error;
  poller logs and skips that record for the current cycle (no state change, no
  false "not available" persisted).
- Seerr duplicate/already-available error on request: surfaced verbatim to the
  user, no local record created.
- Selection view timeout: message edited to indicate timeout, select disabled.
- Any exception inside the poll loop for one record is caught and logged; it must
  not stop the loop from checking the remaining records or from running on the
  next interval.

## Testing

No automated test harness in this repo (consistent with existing project
convention). Manual verification against a live Seerr instance:

1. Configure `OVERSEERR_URL`, `OVERSEERR_API_KEY`, `REQUEST_CHANNEL_ID`; run the
   bot.
2. `/request movie <title>` in the configured channel as a user with an allowed
   role → verify search results appear, selection works, confirmation embed posts,
   and `movie_requests.json` contains the new record.
3. Attempt the command in a different channel and as a user without an allowed
   role → verify both rejections.
4. Request the same movie twice → verify Seerr's duplicate error is surfaced.
5. Mark the requested movie "available" in Seerr (or wait for a real download) →
   verify the poller announces in the announce channel with the correct mention
   within one `REQUEST_POLL_INTERVAL`.
6. Point `OVERSEERR_URL` at an unreachable host → verify the command fails
   gracefully and the poll loop keeps running without crashing.
