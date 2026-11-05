"""Runnable self-check for watch stats (hours, finished titles, persistence).

Run from the repo root:  python tests/test_watch_stats.py
"""
import os
import sys
import tempfile
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.cogs.stats import build_stats_embed, stats_title_key  # noqa: E402
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

    # An upgraded file is the same title; another year or episode is not
    assert stats_title_key(_leaf("Heat (1995) 720p.mkv")) == stats_title_key(_leaf("Heat.1995.2160p.UHD.BluRay.x265.mkv"))
    assert stats_title_key(_leaf("Heat (1995).mkv")) != stats_title_key(_leaf("Heat (2030).mkv"))
    assert stats_title_key(_leaf("Show.S01E01.mkv")) != stats_title_key(_leaf("Show.S01E02.mkv"))
    assert stats_title_key(None) is None
    print("ok")


if __name__ == "__main__":
    main()
