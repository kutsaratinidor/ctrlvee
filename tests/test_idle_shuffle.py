"""Runnable self-check for turning shuffle back on when the voice room is idle.

Run from the repo root:  python tests/test_idle_shuffle.py
"""
import asyncio
import os
import sys
import types
import xml.etree.ElementTree as ET
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.cogs.stats import StatsCommands  # noqa: E402
from src.config import Config  # noqa: E402

STREAMER, VIEWER = 1, 2


def member(uid, bot=False):
    return types.SimpleNamespace(id=uid, bot=bot)


def make_cog(shuffle_on=False, queued=()):
    vlc = mock.Mock(_queued_items=list(queued))
    vlc.get_status.return_value = ET.fromstring(f"<root><random>{'true' if shuffle_on else 'false'}</random></root>")
    sent = []
    channel = mock.Mock()
    channel.send = mock.AsyncMock(side_effect=sent.append)
    bot = mock.Mock()
    bot.get_channel.return_value = channel
    return StatsCommands(bot, vlc, stats=None), vlc, sent


def run(cog, members, clock):
    room = types.SimpleNamespace(members=members)
    with mock.patch("src.cogs.stats.time.monotonic", return_value=clock):
        asyncio.run(cog._idle_shuffle(room))


def main():
    with mock.patch.object(Config, "IDLE_SHUFFLE_MINUTES", 10), \
         mock.patch.object(Config, "STATS_EXCLUDED_USER_IDS", [STREAMER]), \
         mock.patch.object(Config, "COMMAND_CHANNEL_IDS", [99]):
        idle = [member(STREAMER), member(3, bot=True)]

        cog, vlc, sent = make_cog()
        run(cog, idle, 0)
        run(cog, idle, 599)
        assert not vlc.toggle_shuffle.called, "toggled before the idle time passed"
        run(cog, idle, 600)
        assert vlc.toggle_shuffle.call_count == 1 and len(sent) == 1, "didn't turn shuffle on after idle"
        run(cog, idle, 630)
        assert vlc.toggle_shuffle.call_count == 1, "rechecked every tick instead of after another stretch"

        cog, vlc, sent = make_cog()
        run(cog, idle, 0)
        run(cog, idle + [member(VIEWER)], 300)  # a viewer resets the timer
        run(cog, idle, 600)
        run(cog, idle, 899)
        assert not vlc.toggle_shuffle.called, "a viewer joining didn't reset the timer"

        cog, vlc, sent = make_cog(shuffle_on=True)
        run(cog, idle, 0)
        run(cog, idle, 600)
        assert not vlc.toggle_shuffle.called and not sent, "toggled shuffle that was already on"

        cog, vlc, sent = make_cog(queued=[{"id": "5"}])
        run(cog, idle, 0)
        run(cog, idle, 600)
        assert not vlc.toggle_shuffle.called, "fought the soft queue's shuffle handling"

    with mock.patch.object(Config, "IDLE_SHUFFLE_MINUTES", 0):
        cog, vlc, sent = make_cog()
        run(cog, [], 0)
        run(cog, [], 10_000)
        assert not vlc.toggle_shuffle.called, "IDLE_SHUFFLE_MINUTES=0 should turn it off"

    print("idle shuffle: all checks passed")


if __name__ == "__main__":
    main()
