"""Runnable self-check for the schedule list cards (poster, colour by how soon, TMDB cached).

Run from the repo root:  python tests/test_schedule_cards.py
"""
import asyncio
import os
import sys
import types
from datetime import datetime, timedelta
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import discord  # noqa: E402

from src.cogs import scheduler as sch  # noqa: E402


def tmdb_embed():
    embed = discord.Embed(title="Heat (1995)", url="https://www.themoviedb.org/movie/949")
    embed.add_field(name="Rating", value="⭐ 7.9/10").add_field(name="Genre", value="Crime, Drama")
    embed.set_thumbnail(url="https://image.tmdb.org/t/p/w500/heat.jpg")
    return embed


def main():
    now = datetime(2026, 11, 16, 12, 0, tzinfo=sch.PH_TZ)
    soon = {"number": 3, "title": "Heat", "uri": "file:///m/Heat%20(1995).mkv",
            "dt": now + timedelta(minutes=30), "user": 7, "duration": 10200}
    later_today = {"number": 4, "title": "Alien", "dt": now + timedelta(hours=8), "duration": "Loading..."}
    tomorrow = {"number": 5, "title": "Up", "dt": now + timedelta(days=1)}

    tmdb = mock.Mock(lookup_media_embed=mock.Mock(return_value=(tmdb_embed(), True)))
    soon["tmdb"] = sch.tmdb_info(tmdb, soon)
    tmdb.lookup_media_embed.assert_called_once_with("file:///m/Heat (1995).mkv", soon["uri"])
    assert soon["tmdb"]["poster"].endswith("heat.jpg") and soon["tmdb"]["genre"] == "Crime, Drama"

    card = sch.schedule_card(soon, now)
    assert card.title == "Heat (1995)" and card.url.endswith("/949") and card.thumbnail.url.endswith("heat.jpg")
    assert card.color == discord.Color.orange() and "Starting soon" in card.description
    assert "⏱️ 2h 50m · ends" in card.description and "⭐ 7.9/10 · Crime, Drama" in card.description
    assert "Playlist #3 · scheduled by <@7>" in card.description and "12:30 PM PH time" in card.description

    card = sch.schedule_card(later_today, now)  # no TMDB match, runtime unknown
    assert card.title == "Alien" and card.thumbnail.url is None and "⏱️" not in card.description
    assert card.color == discord.Color.green() and "Today" in card.description
    assert sch.schedule_card(tomorrow, now).color == discord.Color.blurple()

    # Lookup errors and no match: {} so the list still shows, without a poster
    tmdb.lookup_media_embed.side_effect = RuntimeError("timeout")
    assert sch.tmdb_info(tmdb, tomorrow) == {}
    tmdb.lookup_media_embed.side_effect, tmdb.lookup_media_embed.return_value = None, (None, False)
    assert sch.tmdb_info(tmdb, tomorrow) == {}

    # The list: soonest first, capped at 10 cards, TMDB looked up once per schedule
    cog = types.SimpleNamespace(
        scheduled=[dict(tomorrow, number=n, dt=now + timedelta(days=n)) for n in range(12, 0, -1)],
        tmdb=mock.Mock(lookup_media_embed=mock.Mock(return_value=(tmdb_embed(), True))),
        _save_schedule_backup=mock.Mock())
    with mock.patch.object(sch, "datetime", wraps=datetime) as fake:
        fake.now.return_value = now
        message = asyncio.run(sch.Scheduler.schedule_list_message(cog))
        assert message["content"] == "🎬 **Upcoming Screenings** · 12 scheduled (showing the next 10)"
        assert len(message["embeds"]) == 10 and "Playlist #1 " in message["embeds"][0].description
        assert cog.tmdb.lookup_media_embed.call_count == 10 and cog._save_schedule_backup.called
        asyncio.run(sch.Scheduler.schedule_list_message(cog))
        assert cog.tmdb.lookup_media_embed.call_count == 10  # cached on the entries
    assert asyncio.run(sch.Scheduler.schedule_list_message(types.SimpleNamespace(scheduled=[]))) == {
        "content": "No movies scheduled."}
    print("ok")


if __name__ == "__main__":
    main()
