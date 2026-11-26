"""Runnable self-check for movie roulette: TV episodes stay out, genre narrows the pool, re-spin moves.

Run from the repo root:  python tests/test_roulette.py
"""
import asyncio
import types
from unittest import mock
import os
import sys
import xml.etree.ElementTree as ET
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.cogs import playlist as pl  # noqa: E402


def leaf(name, n):
    return ET.Element("leaf", {"id": str(100 + n), "name": name, "uri": f"file:///m/{quote(name)}"})


ITEMS = [leaf("Heat.1995.mkv", 1), leaf("Show.S01E02.mkv", 2), leaf("Alien.1979.mkv", 3), leaf("Show.1x03.mkv", 4)]


class FakeVLC:
    def get_playlist(self):
        root = ET.Element("node")
        root.extend(ITEMS)
        return root


class FakeTMDB:
    def get_movie_metadata(self, title, year=None, file_path=None):
        return None  # exercise the plain fallback embed


class FakeGenres:
    def by_genre(self, items):
        return {"Crime": [(1, items[0])], "Horror": [(3, items[2])]}


async def main():
    cog = pl.PlaylistCommands(bot=None, vlc_controller=FakeVLC(), tmdb_service=FakeTMDB(),
                              watch_service=None, genre_service=FakeGenres())
    for _ in range(30):
        reply = await cog.roulette_reply(1, "")
        num, _item = reply["view"].results[0]
        assert num in (1, 3), f"TV episode #{num} drawn"
        assert len(reply["view"].pool) == 2
    reply = await cog.roulette_reply(1, "horr")
    assert reply["view"].results[0][0] == 3 and "Horror" in reply["embed"].author.name
    assert "#3" in reply["embed"].footer.text
    reply = await cog.roulette_reply(1, "western")
    assert "content" in reply and "Genres in the playlist" in reply["content"]

    # One movie: buttons, not one-option dropdowns; Play/Queue act on the drawn movie
    view = (await cog.roulette_reply(1, "horr"))["view"]
    assert not any(isinstance(c, pl.discord.ui.Select) for c in view.children)
    buttons = {c.label: c for c in view.children}
    assert list(buttons) == ['▶️ Play', '⏭️ Queue next', '🎲 Spin again', '✕ Cancel'], list(buttons)
    picked = view.results[0]
    for label, name in (('▶️ Play', '_play'), ('⏭️ Queue next', '_queue')):
        with mock.patch.object(view, name, mock.AsyncMock()) as action:
            await buttons[label].callback(types.SimpleNamespace(user=types.SimpleNamespace(id=1)))
            action.assert_awaited_once()
            assert action.await_args.args[1:] == picked
    stranger = types.SimpleNamespace(user=types.SimpleNamespace(id=2),
                                     response=types.SimpleNamespace(send_message=mock.AsyncMock()))
    with mock.patch.object(view, '_play', mock.AsyncMock()) as action:
        await buttons['▶️ Play'].callback(stranger)
        action.assert_not_awaited()
    print("roulette ok")


asyncio.run(main())
