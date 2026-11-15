"""Runnable self-check for watch stats (hours, finished titles, persistence).

Run from the repo root:  python tests/test_watch_stats.py
"""
import asyncio
import os
import sys
import types
import tempfile
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest import mock  # noqa: E402

import discord  # noqa: E402

from src.cogs.stats import StatsCommands, build_stats_embed, build_stats_embeds, stats_title_key, tmdb_card, view_info  # noqa: E402
from src.services.stats_service import StatsService  # noqa: E402


def _leaf(filename):
    return ET.Element("leaf", {"name": filename, "uri": "file:///movies/" + filename.replace(" ", "%20")})


def main():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "stats.json")
        stats = StatsService(path)
        assert build_stats_embed(stats).description == "No watch time recorded yet."

        ann, bob = {1: "Ann"}, {2: "Bob"}
        length = 600  # 10 minutes: 8.5 minutes seen is needed, so 9 buckets

        # Rewatching one minute adds hours but no progress
        for _ in range(20):
            stats.record(ann, "heat|1995", 30, length, 30)
        assert stats.users["1"]["seconds"] == 600
        assert stats.users["1"]["finished"] == []
        assert stats.users["1"]["progress"]["heat|1995"] == [0]

        # Progress survives a restart, so two nights add up
        for minute in range(1, 5):
            stats.record(ann, "heat|1995", minute * 60, length, 30)
        stats.save()
        stats = StatsService(path)
        for minute in range(5, 8):
            stats.record(ann, "heat|1995", minute * 60, length, 30)
        assert stats.users["1"]["finished"] == []
        stats.record(ann, "heat|1995", 8 * 60, length, 30)
        assert stats.users["1"]["finished"] == ["heat|1995"]
        assert "heat|1995" not in stats.users["1"]["progress"]

        # A replay adds hours but never a second finish
        for minute in range(10):
            stats.record(ann, "heat|1995", minute * 60, length, 30)
        assert stats.users["1"]["finished"] == ["heat|1995"]

        # Someone who skips most of the movie doesn't finish it
        stats.record(bob, "heat|1995", 590, length, 30)
        assert stats.users["2"]["finished"] == []

        # No runtime or no title (a live stream): hours only
        stats.record(bob, None, 0, 0, 30)
        stats.record(bob, "stream|", 0, 0, 30)
        assert stats.users["2"]["seconds"] == 90
        assert stats.users["2"]["progress"] == {"heat|1995": [9]}

        # Renames follow the user id; ladders rank by each measure
        stats.record({2: "Robert"}, None, 0, 0, 30)
        hours, finished = stats.ladders()
        assert hours[0][0] == "Ann" and hours[1] == ("Robert", 120)
        assert finished == [("Ann", 1)]
        embed = build_stats_embed(stats)
        assert "1. Ann — " in embed.fields[0].value and "2. Robert — 0.0 h" in embed.fields[0].value
        assert embed.fields[1].value == "1. Ann — 1"

        # Excluded users leave both ladders, and their totals stay in the file
        stats.excluded = {"1"}
        hours, finished = stats.ladders()
        assert [name for name, _ in hours] == ["Robert"] and finished == []
        assert stats.users["1"]["finished"] == ["heat|1995"]
        assert StatsService(path, excluded=[1]).excluded == {"1"}

        # A damaged file is kept aside, not overwritten by the next save
        with open(path, "w", encoding="utf-8") as f:
            f.write("{broken")
        assert StatsService(path).users == {}
        assert os.path.exists(f"{path}.corrupt")

    check_recently_viewed()
    check_tick()

    # An upgraded file is the same title; another year or episode is not
    assert stats_title_key(_leaf("Heat (1995) 720p.mkv")) == stats_title_key(_leaf("Heat.1995.2160p.UHD.BluRay.x265.mkv"))
    assert stats_title_key(_leaf("Heat (1995).mkv")) != stats_title_key(_leaf("Heat (2030).mkv"))
    assert stats_title_key(_leaf("Show.S01E01.mkv")) != stats_title_key(_leaf("Show.S01E02.mkv"))
    assert stats_title_key(None) is None
    print("ok")


def check_recently_viewed():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "stats.json")
        stats = StatsService(path, excluded=[9])
        streamer, ann = {9: "Streamer"}, {1: "Ann"}
        heat = view_info(_leaf("Heat (1995) 1080p.mkv"))
        assert heat[1:] == ("Heat (1995)", None)

        # Only the excluded streamer in the room: played, not viewed
        for _ in range(20):
            assert stats.record_view(*heat, streamer, 30) is None
        assert stats.recent == [] and build_stats_embeds(stats)[1:] == []

        # A short drop-in doesn't count; 5 minutes does, and only real viewers are listed
        for _ in range(9):
            stats.record_view(*heat, {**streamer, **ann}, 30)
        assert stats.recently_viewed() == []
        entry = stats.record_view(*heat, {**streamer, **ann}, 30)
        assert [e["key"] for e in stats.recently_viewed()] == [heat[0]]
        assert entry["viewers"] == {"1": "Ann"}

        # Episodes of a season share one entry that shows the latest episode, newest first
        ep1 = view_info(_leaf("Show.Name.S02E03.1080p.mkv"))
        ep2 = view_info(_leaf("Show.Name.S02E04.1080p.mkv"))
        assert ep1[0] == ep2[0] and ep2[2] == "S02E04"
        for info in (ep1,) * 5 + (ep2,) * 5:
            stats.record_view(*info, {2: "Bob"}, 30)
        recent = stats.recently_viewed()
        assert [e["key"] for e in recent] == [ep1[0], heat[0]] and recent[0]["episode"] == "S02E04"

        # Rewatching moves a title back to the top, and the list survives a restart
        stats.record_view(*heat, ann, 30)
        stats.save()
        stats = StatsService(path, excluded=[9])
        assert [e["key"] for e in stats.recently_viewed()] == [heat[0], ep1[0]]

        # Cards: poster, link and episode when known; the plain name when TMDB had no match
        stats.recent[0].update(title="Heat", url="https://www.themoviedb.org/movie/949", poster="https://image.tmdb.org/p.jpg")
        stats.recent[1]["poster"] = None
        stats.users = {"1": {"name": "Ann", "seconds": 60, "finished": [], "progress": {}}}
        board, heat_card, show_card = build_stats_embeds(stats)
        assert board.footer.text == "Recently viewed ↓"
        assert heat_card.title == "Heat" and heat_card.thumbnail.url == "https://image.tmdb.org/p.jpg"
        assert heat_card.url.endswith("/949") and "Ann" in heat_card.description
        assert show_card.title == "Show Name" and show_card.thumbnail.url is None
        assert "S02E04" in show_card.description and "Bob" in show_card.description

    # TMDB lookups: SxxEyy goes to TV, anything else to movies; no match is remembered as no poster
    tmdb = mock.Mock()
    tmdb.get_movie_metadata.return_value = None
    assert tmdb_card(tmdb, _leaf("Heat (1995).mkv")) == {"poster": None}
    tmdb.get_movie_metadata.assert_called_once()
    tmdb.get_tv_metadata.return_value.thumbnail.url = "x"
    assert tmdb_card(tmdb, _leaf("Show.Name.S02E03.mkv"))["poster"] == "x"
    assert tmdb.get_tv_metadata.call_args.args[:2] == ("Show Name", 2)


class Room(discord.VoiceChannel):
    members = []

    def __init__(self, members):
        self.__dict__["members"] = members


def check_tick():
    """The 30s tick: the poster is looked up once, after 5 minutes, and saved with the entry."""
    from src.config import Config

    with tempfile.TemporaryDirectory() as tmp:
        stats = StatsService(os.path.join(tmp, "stats.json"), excluded=[9])
        people = [types.SimpleNamespace(id=i, display_name=n, bot=False) for i, n in ((9, "Streamer"), (1, "Ann"))]
        bot = types.SimpleNamespace(get_channel=lambda _id: Room(people))
        vlc = mock.Mock()
        vlc.get_status.return_value = ET.fromstring("<root><state>playing</state><time>60</time><length>6000</length></root>")
        playlist = ET.Element("node")
        playlist.append(_leaf("Heat (1995).mkv"))
        playlist[0].set("current", "current")
        vlc.get_playlist.return_value = playlist
        tmdb = mock.Mock()
        tmdb.get_movie_metadata.return_value = discord.Embed(title="Heat", url="https://t/949").set_thumbnail(url="https://p")
        cog = StatsCommands(bot, vlc, stats, tmdb)

        async def ticks(n):
            for _ in range(n):
                await cog._track.coro(cog)

        with mock.patch.object(Config, "VOICE_JOIN_CHANNEL_ID", 1):
            asyncio.run(ticks(9))
            tmdb.get_movie_metadata.assert_not_called()
            asyncio.run(ticks(5))
        tmdb.get_movie_metadata.assert_called_once()
        saved = StatsService(stats.path).recently_viewed()
        assert saved[0]["poster"] == "https://p" and saved[0]["viewers"] == {"1": "Ann"}, saved


if __name__ == "__main__":
    main()
