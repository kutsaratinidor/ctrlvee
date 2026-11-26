"""Runnable self-check for the queue status poster cards.

Run from the repo root:  python tests/test_queue_cards.py
"""
import asyncio
import os
import sys
import xml.etree.ElementTree as ET
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.cogs import playback as pb  # noqa: E402


def leaf(n, name, duration):
    return ET.Element("leaf", {"id": str(n), "name": name, "uri": f"file:///m/{n}.mkv", "duration": str(duration)})


class FakeVLC:
    def __init__(self, queued, state="playing"):
        self.queued, self.state = queued, state

    def get_queue_status(self):
        return {"queued_items": self.queued}

    def get_status(self):
        root = ET.Element("root")
        for tag, text in (("state", self.state), ("length", "1000"), ("time", "400")):
            ET.SubElement(root, tag).text = text
        return root

    def get_playlist(self):
        root = ET.Element("node")
        root.extend([leaf(1, "Heat.1995.mkv", 6000), leaf(2, "Alien.1979.mkv", 0), leaf(3, "Up.2009.mkv", 5400)])
        return root


class FakeTMDB:
    pass


def make_cog(vlc):
    cog = pb.PlaybackCommands.__new__(pb.PlaybackCommands)  # skip the monitoring setup in __init__
    cog.vlc, cog.tmdb, cog._queue_tmdb = vlc, FakeTMDB(), {}
    return cog


async def main():
    lookups = []

    def fake_info(tmdb, entry):
        lookups.append(entry["uri"])
        return {"title": "Heat", "poster": "https://img/heat.jpg", "rating": "⭐ 8.3", "genre": "Crime"} if "/1.mkv" in (entry["uri"] or "") else {}

    # Queued out of dict order: queue_order decides play order
    queued = {"3": {"queue_order": 3}, "1": {"queue_order": 1}, "2": {"queue_order": 2}}
    with mock.patch.object(pb, "tmdb_info", fake_info), \
         mock.patch.object(pb.MediaUtils, "probe_duration", return_value=None), \
         mock.patch.object(pb.time, "time", return_value=1_000_000):
        cog = make_cog(FakeVLC(queued))
        msg = await cog.queue_status_message("`/queue add-next`")
        first, second, third = msg["embeds"]
        assert "3 items" in msg["content"]
        assert first.title == "Heat" and first.thumbnail.url == "https://img/heat.jpg"
        assert "Up next" in first.description and f"<t:{1_000_000 + 600}:t>" in first.description
        assert "⭐ 8.3 · Crime" in first.description and "Queue #1 · playlist #1" in first.description
        assert first.color == pb.discord.Color.green() and second.color == pb.discord.Color.blurple()
        assert f"<t:{1_000_000 + 600 + 6000}:t>" in second.description  # after Heat's runtime
        assert "starts" not in third.description  # Alien's runtime is unknown, so no guess after it
        assert "Alien" in second.title and "playlist #2" in second.description
        assert len(lookups) == 3
        await cog.queue_status_message("x")
        assert len(lookups) == 3, "TMDB details should be cached per file"

        cog.vlc.state = "paused"
        msg = await cog.queue_status_message("x")
        assert all("starts" not in e.description for e in msg["embeds"])  # no start times unless playing

        cog.vlc.queued = {}
        msg = await cog.queue_status_message("`/queue add-next`")
        assert "Nothing queued" in msg["content"] and "/queue add-next" in msg["content"]

        cog.vlc.queued = {str(n): {"queue_order": n, "item_name": f"Gone {n}"} for n in range(10, 22)}
        msg = await cog.queue_status_message("x")
        assert len(msg["embeds"]) == 10 and "showing the next 10" in msg["content"]
    print("queue cards ok")


asyncio.run(main())
