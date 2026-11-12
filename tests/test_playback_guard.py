"""Runnable self-check for the voice-room guard: real guard, commands that use it, and the prompt.

Run from the repo root:  python tests/test_playback_guard.py
"""
import ast
import asyncio
import os
import sys
import types
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import discord  # noqa: E402

from src.cogs import playback as pb  # noqa: E402
from src.cogs.scheduler import Scheduler  # noqa: E402
from src.config import Config  # noqa: E402
from src.services.vlc_controller import VLCController  # noqa: E402

P = pb.PlaybackCommands
ROOM_ID = 100


class Voice(discord.VoiceChannel):
    def __init__(self, channel_id, name="Room"):
        self.id = channel_id
        self.name = name

    def __repr__(self):
        return self.name


class Guild:
    id = 1

    def __init__(self, channel=None, members=()):
        self.channel = channel
        self.members = {m.id: m for m in members}

    def get_channel(self, channel_id):
        return self.channel if self.channel is not None and self.channel.id == channel_id else None

    def get_member(self, member_id):
        return self.members.get(member_id)


def _member(member_id, guild, channel=None):
    voice = types.SimpleNamespace(channel=channel) if channel else None
    return types.SimpleNamespace(id=member_id, guild=guild, display_name=f"u{member_id}", voice=voice, mention=f"<@{member_id}>")


def _cog(guild, owner=None, owner_item=None, current="7"):
    cog = P.__new__(P)
    cog.bot = types.SimpleNamespace(get_channel=guild.get_channel, get_cog=lambda name: None)
    cog.vlc = mock.Mock()
    cog.last_state_change = {}
    cog._playback_owner_id = owner
    cog._playback_owner_item_id = owner_item
    cog._current_stream_item_id = lambda: current
    return cog


def _guard(channel_id, guild, member, presence_only=False, **kwargs):
    with mock.patch.object(Config, "VOICE_JOIN_CHANNEL_ID", channel_id):
        return _cog(guild, **kwargs)._playback_guard(member, presence_only=presence_only)


def check_guard():
    room, other = Voice(ROOM_ID), Voice(200, "Other")
    guild = Guild(room)
    assert not _guard(ROOM_ID, guild, _member(1, guild))[0]
    assert not _guard(ROOM_ID, guild, _member(1, guild, other))[0]
    assert _guard(ROOM_ID, guild, _member(1, guild, room))[0]

    # No room configured stays open; a configured room that can't be found does not
    assert _guard(0, guild, _member(1, guild))[0]
    for broken_id, broken_guild in ((999, guild), (ROOM_ID, Guild(types.SimpleNamespace(id=ROOM_ID, name="text")))):
        allowed, reason = _guard(broken_id, broken_guild, _member(1, broken_guild))
        assert not allowed and "VOICE_JOIN_CHANNEL_ID" in reason

    with mock.patch.object(Config, "ENABLE_VOICE_ROOM_RULES", False):
        assert _guard(999, guild, _member(1, guild))[0]

    # Presence-only skips the "someone else is watching" check but not the room check
    owner = _member(2, guild, room)
    owned = Guild(room, [owner])
    claim = {"owner": 2, "owner_item": "7"}
    assert not _guard(ROOM_ID, owned, _member(1, owned, room), **claim)[0]
    assert _guard(ROOM_ID, owned, _member(1, owned, room), presence_only=True, **claim)[0]
    assert not _guard(ROOM_ID, owned, _member(1, owned), presence_only=True, **claim)[0]


def check_prefix_commands():
    """Outside the room these never reach VLC, and pause still does."""
    guild = Guild(Voice(ROOM_ID))
    outsider = _member(1, guild)

    async def run(command, *args):
        cog = _cog(guild)
        ctx = types.SimpleNamespace(author=outsider, guild=guild, send=mock.AsyncMock())
        await command.callback(cog, ctx, *args)
        return cog, ctx

    async def main():
        with mock.patch.object(Config, "VOICE_JOIN_CHANNEL_ID", ROOM_ID):
            for command, args, method in (
                (P.play, (), "play"), (P.stop, (), "stop"), (P.restart, (), "seek"),
                (P.rewind, (10,), "seek"), (P.forward, (99999,), "seek"), (P.speed, ("1.5",), "set_rate"),
                (P.shuffle_on, (), "toggle_shuffle"), (P.shuffle_off, (), "toggle_shuffle"),
                (P.shuffle_toggle, (), "toggle_shuffle"), (P.clear_queue, (), "clear_queue_tracking"),
                (P.remove_queue, ("1",), "remove_from_queue_by_order"),
            ):
                cog, ctx = await run(command, *args)
                getattr(cog.vlc, method).assert_not_called()
                assert "must be in" in ctx.send.call_args.args[0], command.name

            # Speed with no argument only shows usage, so it stays open
            _, ctx = await run(P.speed)
            assert "embed" in ctx.send.call_args.kwargs

            cog = _cog(guild)
            cog.vlc.get_status.return_value = types.SimpleNamespace(find=lambda tag: types.SimpleNamespace(text="playing"))
            cog.vlc.pause.return_value = False
            ctx = types.SimpleNamespace(author=outsider, guild=guild, send=mock.AsyncMock())
            await P.pause.callback(cog, ctx)
            cog.vlc.pause.assert_called_once()

    asyncio.run(main())


def check_schedule_prompt():
    """The guard runs again after Continue, because the prompt can wait 30 seconds."""
    room = Voice(ROOM_ID)
    guild = Guild(room)

    async def confirm(member, clicked, leave_during_prompt=False):
        cog = _cog(guild)
        cog._nearby_schedule_notice = lambda *args: "heads up"
        sendable = types.SimpleNamespace(send=mock.AsyncMock())

        class View:
            message = None

            def __init__(self, *args, **kwargs):
                self.decision = None

            async def wait(self):
                if leave_during_prompt:
                    member.voice = None
                self.decision = clicked

        with mock.patch.object(pb, "ScheduleConfirmView", View):
            return await cog._confirm_nearby_schedule(sendable, member), sendable

    async def main():
        with mock.patch.object(Config, "VOICE_JOIN_CHANNEL_ID", ROOM_ID):
            assert (await confirm(_member(1, guild, room), True))[0] is True
            assert (await confirm(_member(1, guild, room), False))[0] is False
            allowed, sendable = await confirm(_member(1, guild, room), True, leave_during_prompt=True)
            assert allowed is False
            assert "must be in" in sendable.send.call_args.args[0]

    asyncio.run(main())


def check_schedule_and_pause():
    for command in (Scheduler.schedule_movie, Scheduler.list_schedules, Scheduler.unschedule):
        assert command.checks, f"!{command.name} has no role check"

    # VLC's pl_pause is a toggle and resumes; pause must never start playback
    vlc = VLCController.__new__(VLCController)
    vlc.send_command = mock.Mock(return_value=True)
    vlc.pause()
    vlc.send_command.assert_called_once_with("pl_forcepause")


def check_slash_handlers():
    bot_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bot.py")
    with open(bot_path, encoding="utf-8") as f:
        handlers = {n.name: ast.unparse(n) for n in ast.parse(f.read()).body if isinstance(n, ast.AsyncFunctionDef)}
    role, room = "_check_allowed_roles_for_interaction", "_check_room_for_interaction"
    for name in ("playback_play", "playback_stop", "playback_restart", "playback_rewind", "playback_forward",
                 "playback_speed", "playback_shuffle", "queue_clear", "queue_remove"):
        assert role in handlers[name] and room in handlers[name], name
    # Schedules can be planned from outside the room
    for name in ("schedule_add", "schedule_list", "schedule_remove"):
        assert role in handlers[name] and room not in handlers[name], name
    assert room not in handlers["playback_pause"]


def main():
    check_guard()
    check_prefix_commands()
    check_schedule_prompt()
    check_schedule_and_pause()
    check_slash_handlers()
    print("ok")


if __name__ == "__main__":
    main()
