"""Runnable self-check for the soft queue against a fake VLC, driving the real monitor loop.

Run from the repo root:  python tests/test_soft_queue.py
"""
import asyncio
import os
import sys
import tempfile
import types
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.cogs import playback as pb  # noqa: E402
from src.services.vlc_controller import VLCController  # noqa: E402


class FakeVLC(VLCController):
    """VLC 3 HTTP semantics that matter here: childless <leaf/> elements, pl_play ignores unknown ids."""

    def __init__(self):
        super().__init__(queue_backup_file=tempfile.mktemp())
        self.items = {"1": "a", "2": "b", "3": "c", "4": "d", "5": "e"}
        self.cur, self.state, self.random, self.rate = "1", "playing", False, 1.0

    def _make_request(self, endpoint, params=None):
        params = params or {}
        cmd = params.get("command")
        if cmd == "pl_play" and params.get("id") in self.items:
            self.cur, self.state = params["id"], "playing"
        elif cmd == "pl_play" and "id" not in params:
            self.state = "playing"
        elif cmd == "pl_stop":
            self.state = "stopped"
        elif cmd == "pl_next":
            self.advance()
        elif cmd == "pl_random":
            self.random = not self.random
        if endpoint == "playlist.xml":
            root = ET.Element("node")
            for item_id, name in self.items.items():
                attrs = {"id": item_id, "name": name}
                if item_id == self.cur:
                    attrs["current"] = "current"
                ET.SubElement(root, "leaf", attrs)
            return root
        status = ET.Element("root")
        ET.SubElement(status, "state").text = self.state
        ET.SubElement(status, "random").text = str(self.random).lower()
        ET.SubElement(status, "time").text = "100"
        ET.SubElement(status, "length").text = "5000"
        return status

    def advance(self):
        ids = list(self.items)
        self.cur = ids[(ids.index(self.cur) + 1) % len(ids)]

    def set_rate(self, rate):
        self.rate = rate
        return True


async def _noop(*a, **k):
    return None


def _cog(vlc):
    cog = pb.PlaybackCommands.__new__(pb.PlaybackCommands)
    cog.bot = types.SimpleNamespace(wait_until_ready=_noop, get_channel=lambda i: None)
    cog.vlc, cog.logger = vlc, pb.logger
    for k, v in dict(last_known_state=None, last_known_position=None, last_known_playing_item=None,
                     _initial_scan_pending=False, playback_started_event=asyncio.Event(), last_queue_auto_play=-10,
                     _command_initiated_change=False, _last_command_announce_ts=0, _auto_suppress_seconds=5,
                     _suppress_auto_announce_until=0).items():
        setattr(cog, k, v)
    cog._set_presence = cog._set_voice_channel_status = cog._announce_now_playing = _noop
    return cog


async def ticks(cog, n=2):
    left = {"n": n}

    def closed():
        left["n"] -= 1
        return left["n"] < 0
    cog.bot.is_closed = closed
    await cog._monitor_vlc_state()


async def main():
    vlc = FakeVLC()
    cog = _cog(vlc)
    await ticks(cog, 1)

    vlc.queue_item_next("3")
    await ticks(cog)
    assert vlc.cur == "1" and list(vlc._queued_items) == ["3"], "queueing must not interrupt what's playing"

    vlc.next()  # what /playback next (slash) and !next both do
    await ticks(cog)
    assert vlc.cur == "3" and not vlc._queued_items, f"next must play the queued item, got {vlc.cur}"

    vlc.queue_item_next("5")
    vlc.queue_item_next("2")
    vlc.advance()  # VLC auto-advances to the playlist's next item when a file ends
    await ticks(cog)
    assert vlc.cur == "5" and list(vlc._queued_items) == ["2"], f"auto-advance must yield to the queue head, got {vlc.cur}"
    vlc.advance()
    await ticks(cog)
    assert vlc.cur == "2" and not vlc._queued_items, f"second queued item must follow in order, got {vlc.cur}"

    vlc.queue_item_next("4")
    vlc.play_item("1")  # explicit /playback play 1 is not hijacked by the queue
    await ticks(cog)
    assert vlc.cur == "1" and list(vlc._queued_items) == ["4"], "explicit play must win; queue stays pending"
    vlc.clear_queue_tracking()

    vlc.queue_item_next("3")
    vlc.stop()  # a user stop sticks while something is queued
    await ticks(cog, 6)
    assert vlc.state == "stopped" and vlc.cur == "1" and list(vlc._queued_items) == ["3"], "stop must not be undone by the queue"
    vlc.play()  # resuming continues the stopped item, queue still waits
    await ticks(cog)
    assert vlc.cur == "1" and not vlc.queue_held and list(vlc._queued_items) == ["3"], "resume must release the hold, not skip"
    vlc.state = "stopped"  # the file ends and VLC stops on its own
    await ticks(cog)
    assert vlc.cur == "3" and vlc.state == "playing", "a natural stop must still start the queue"
    vlc.clear_queue_tracking()

    announced = []

    async def announce(source, item, position):
        announced.append(item.get('id'))
    cog._announce_now_playing = announce
    vlc.items = {"1": "a", "2": "b", "3": "c", "4": "d", "5": "e"}
    vlc.cur, vlc.state = "4", "playing"
    await ticks(cog)
    announced.clear()
    vlc.queue_item_next("5")
    for gone in ("1", "2", "3"):  # cleanup deletes missing files above the current item, one per poll
        del vlc.items[gone]
        await ticks(cog, 1)
    assert not announced, f"cleanup shifting positions must not re-announce Now Playing, got {announced}"
    assert vlc.cur == "4" and list(vlc._queued_items) == ["5"], "cleanup must not trigger queue auto-play"
    vlc.items = {"1": "a", "2": "b", "3": "c", "4": "d", "5": "e"}
    vlc.cur = "1"
    vlc.clear_queue_tracking()
    await ticks(cog)
    cog._announce_now_playing = _noop

    vlc.set_rate(1.5)
    await ticks(cog)
    assert vlc.rate == 1.5, "speed must hold while the same item plays"
    vlc.advance()  # file ends, VLC moves on
    await ticks(cog)
    assert vlc.rate == 1.0, "speed must reset to 1.0 when a new item starts"

    vlc.queue_item_next("99")  # id no longer in VLC (playlist reloaded / VLC restarted)
    vlc.queue_item_next("3")
    vlc._queued_items["3"]["item_name"] = "renamed"  # same id now points at a different file
    vlc.queue_item_next("4")
    result = vlc.next()
    assert result.get("item_id") == "4" and vlc.cur == "4" and not vlc._queued_items, "stale queue entries must be skipped"

    print("soft queue: all checks passed")


if __name__ == "__main__":
    asyncio.run(main())
