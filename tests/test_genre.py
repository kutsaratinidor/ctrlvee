"""Runnable self-check for browsing the playlist by genre (service, TMDB fallback, picker).

Run from the repo root:  python tests/test_genre.py
"""
import asyncio
import json
import os
import sys
import tempfile
import time
import types
import xml.etree.ElementTree as ET
from unittest import mock
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import discord  # noqa: E402

from src.cogs import playlist as pl  # noqa: E402
from src.services import genre_service as gs  # noqa: E402
from src.services import tmdb_service as tmdb_mod  # noqa: E402
from src.services.tmdb_service import TMDBService  # noqa: E402


def leaf(filename, n=0, folder="/Volumes/media/Movies"):
    """A VLC playlist leaf: URL-encoded file URI, mounted somewhere other than where Radarr sees it."""
    return ET.Element("leaf", {"id": str(100 + n), "name": filename, "uri": f"file://{quote(folder)}/{quote(filename)}"})


def radarr_movie(title, year, path, *genres):
    return {"title": title, "year": year, "genres": list(genres), "movieFile": {"path": path} if path else None}


class FakeRadarr:
    def __init__(self, movies=None, error=None, host="radarr"):
        self.movies, self.error, self.host, self.calls = movies or [], error, host, 0

    def is_configured(self):
        return True

    def list_movies(self):
        self.calls += 1
        if self.error:
            raise self.error
        return self.movies


class FakeTMDB:
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def movie_genres(self, title, year=None):
        self.calls.append((title, year))
        return self.answers.get(title, [])


def check_parsing():
    assert gs.title_key("Heat.1995.1080p.BluRay.x264.mkv") == gs.title_key("Heat (1995)") == "heat|1995"
    assert gs.title_key("Léon: The Professional (1994)") == gs.title_key("Leon.The.Professional.1994.mkv")
    assert gs.title_key("Le\u0301on (1994)") == gs.title_key("Léon (1994)")  # macOS's decomposed accents
    assert gs.title_key("movie.mkv") == "movie|" and gs.title_key("") is None
    for name, movie in (
        ("Heat.1995.1080p.mkv", True),
        ("The.Humans.2021.1080p.BluRay.DD+5.1x264-iFT.mkv", True),  # not a "1x26" episode
        ("Josee (2020) [BDRip 1920x804 HEVC].mkv", True),  # not a "20x80" episode
        ("Odd.Film.2001.webm", True),  # unknown extensions count as video
        ("Show.Name.S01E02.mkv", False),
        ("Show Name 1x02.mkv", False),
        ("Soundtrack (1995).flac", False),
        ("Heat (1995).srt", False),
    ):
        assert gs.is_movie(leaf(name)) is movie, name
    # Streams and other non-file URIs aren't library movies; plain and Windows paths are
    assert not gs.is_movie(ET.Element("leaf", {"name": "Live", "uri": "http://example.com/live.mkv"}))
    assert not gs.is_movie(ET.Element("leaf", {"name": "Cam", "uri": "rtsp://cam/stream"}))
    assert gs.is_movie(ET.Element("leaf", {"name": "x", "uri": r"C:\Films\Heat (1995).mkv"}))
    assert gs.is_movie(ET.Element("leaf", {"name": "/films/Heat (1995).mkv"}))
    assert not gs.is_movie(ET.Element("leaf", {}))

    assert gs.match_genre("comedy", ["Comedy", "Drama"]) == "Comedy"
    assert gs.match_genre("sci", ["Science Fiction", "Drama"]) == "Science Fiction"  # unique start
    assert gs.match_genre("fic", ["Science Fiction", "Drama"]) == "Science Fiction"  # unique substring
    assert gs.match_genre("a", ["Action", "Animation"]) is None  # ambiguous
    assert gs.match_genre("Action", ["Action", "Action Comedy"]) == "Action"  # exact wins
    assert gs.match_genre("ACCION", ["Acción"]) == "Acción"  # accents and case
    assert gs.match_genre("  ", ["Action"]) is None


def check_service():
    with tempfile.TemporaryDirectory() as tmp:
        cache_path = os.path.join(tmp, "genre_cache.json")
        main = FakeRadarr([
            radarr_movie("Heat", 1995, "/movies/Heat (1995)/Heat.1995.1080p.BluRay.x264.mkv", "Crime", "Action"),
            radarr_movie("Alien", 1979, None, "Horror", "Science Fiction"),  # no file: matched by title
            {"title": "Odd", "year": 2000, "genres": [{"name": "Drama"}], "movieFile": {"path": r"D:\Films\Odd\Odd.2000.mkv"}},
        ])
        broken = FakeRadarr(error=RuntimeError("500"), host="anime")
        tmdb = FakeTMDB({"Dune": ["Science Fiction", "Adventure"], "Flaky": None})
        service = gs.GenreService([main, broken], tmdb, path=cache_path)

        # One failing instance doesn't stop the others
        service.refresh_radarr()
        assert main.calls == 1 and broken.calls == 1
        items = [
            leaf("Heat.1995.1080p.BluRay.x264.mkv", 0),          # by file, despite a different mount
            leaf("Alien (1979) Directors Cut.mkv", 1),          # by title + year
            leaf("Odd.2000.mkv", 2, folder="/x/Odd"),           # Windows path in Radarr
            leaf("Dune.2021.2160p.mkv", 3),                     # not in Radarr: TMDB
            leaf("Unknown.Film.2010.mkv", 4),                   # TMDB has no match
            leaf("Flaky.2015.mkv", 5),                          # TMDB errors
            leaf("Show.Name.S01E02.mkv", 6),                    # an episode: never listed
        ]
        assert service.genres_for(items[0]) == ["Crime", "Action"]
        assert service.genres_for(items[1]) == ["Horror", "Science Fiction"]
        assert service.genres_for(items[2]) == ["Drama"]
        assert service.genres_for(items[3]) is None and service.genres_for(items[6]) is None

        # TMDB only for unknown movies, a limited number per run, with the file's own capitals;
        # an error isn't cached
        assert service.fill_from_tmdb(items, limit=2) == 2
        assert tmdb.calls == [("Dune", 2021), ("Unknown Film", 2010)]
        assert service.fill_from_tmdb(items) == 1 and tmdb.calls[-1] == ("Flaky", 2015)
        assert service.genres_for(items[3]) == ["Science Fiction", "Adventure"]
        assert service.genres_for(items[4]) == [] and service.genres_for(items[5]) is None
        with open(cache_path, encoding="utf-8") as f:
            saved = json.load(f)
        assert {k: v["genres"] for k, v in saved.items()} == {"dune|2021": ["Science Fiction", "Adventure"], "unknown film|2010": []}

        # A restart keeps TMDB answers; the error is tried again, the no-match isn't (yet)
        service = gs.GenreService([main], tmdb, path=cache_path)
        assert service.genres_for(items[3]) == ["Science Fiction", "Adventure"]
        service.refresh_radarr()  # the background loop always loads Radarr before asking TMDB
        assert service.fill_from_tmdb(items) == 1 and tmdb.calls[-1] == ("Flaky", 2015)
        # A no-match is asked again after 30 days, in case TMDB has added it
        service.tmdb_cache["unknown film|2010"]["at"] -= gs.TMDB_NO_MATCH_RETRY_SECONDS + 1
        tmdb.answers["Unknown Film"] = ["Thriller"]
        service.fill_from_tmdb(items)
        assert service.genres_for(items[4]) == ["Thriller"]

        # Index: playlist numbers, movies only, each genre sorted by title
        index = service.by_genre(items)
        assert sorted(index) == ["Action", "Adventure", "Crime", "Drama", "Horror", "Science Fiction", "Thriller"]
        assert [n for n, _ in index["Science Fiction"]] == [2, 4]  # Alien, Dune
        assert all(n != 7 for entries in index.values() for n, _ in entries)

        # A damaged or old-format cache starts empty instead of crashing
        for content in ("{broken", "[1, 2]", '{"heat|1995": ["Crime"]}'):
            with open(cache_path, "w", encoding="utf-8") as f:
                f.write(content)
            assert gs.GenreService([], None, path=cache_path).tmdb_cache == {}


def check_radarr_edge_cases():
    with tempfile.TemporaryDirectory() as tmp:
        cache_path = os.path.join(tmp, "genre_cache.json")
        tmdb = FakeTMDB({})
        flaky = FakeRadarr([
            radarr_movie("Heat", 1995, "/m/Heat (1995)/Heat.1995.mkv", "Crime"),
            radarr_movie("Ronin", 1998, "/m/Ronin (1998)/Ronin.1998.mkv", "Action"),  # only in this instance
        ], host="flaky")
        other = FakeRadarr([
            # Two different movies, same title and year, different genres: neither is trusted by title
            radarr_movie("Metamorphosis", 2019, "/m/Metamorphosis (2019)/Metamorphosis.2019.mkv", "Horror", "Thriller"),
            radarr_movie("Metamorphosis", 2019, None, "Drama"),
            # Same title and year in two instances with the same genres: fine
            radarr_movie("Heat", 1995, None, "Crime"),
            # No genres in Radarr yet: left to TMDB rather than hidden
            radarr_movie("Dollhouse", 2025, "/m/Dollhouse (2025)/Dollhouse.2025.mkv"),
            # Announced, no year: must not match a playlist file that has no year in its name
            radarr_movie("Avatar 5", 0, None, "Science Fiction"),
            # ...nor, when the title has a number in it, a file that reads that number as a year
            radarr_movie("Blade Runner 2049", 0, None, "Romance"),
            # Original title (romaji) matches files named that way
            {"title": "Your Name.", "originalTitle": "Kimi no Na wa.", "year": 2016, "genres": ["Animation", "Romance"]},
            # relativePath only, messy genres (spaces, repeats, odd types)
            {"title": "Rel", "year": 2001, "path": "/m/Rel (2001)", "movieFile": {"relativePath": "Rel.2001.mkv"},
             "genres": [" Drama ", "drama", None, 5, {"name": "War"}, {}]},
            # Generic file names in different folders, with different genres
            radarr_movie("Alpha", 2003, "/m/Alpha (2003)/movie.mkv", "Action"),
            radarr_movie("Beta", 2004, "/m/Beta (2004)/movie.mkv", "Comedy"),
            radarr_movie("Gamma", 2005, "/m/Gamma (2005)/video.mp4", "Western"),  # generic, and unique
            "not a movie", None,
        ])
        service = gs.GenreService([flaky, other], tmdb, path=cache_path)
        service.refresh_radarr()

        get = lambda name, folder="/v/m": service.genres_for(leaf(name, folder=folder))
        assert get("Metamorphosis.2019.mkv", "/v/Metamorphosis (2019)") == ["Horror", "Thriller"]  # by file
        assert get("Metamorphosis (2019) copy.mkv") is None  # by title: ambiguous, so TMDB decides
        assert get("Heat 1995 remux.mkv") == ["Crime"]
        assert get("Dollhouse.2025.mkv", "/v/Dollhouse (2025)") is None  # no Radarr genres: TMDB
        assert get("Avatar.5.mkv") is None
        assert get("Blade.Runner.2049.mkv") is None
        assert get("Kimi.no.Na.wa.2016.mkv") == ["Animation", "Romance"]
        assert get("Rel.2001.mkv", "/v/Rel (2001)") == ["Drama", "War"]
        assert get("movie.mkv", "/v/Alpha (2003)") == ["Action"]  # folder + file
        assert get("movie.mkv", "/v/Beta (2004)") == ["Comedy"]
        assert get("movie.mkv", "/v/Somewhere") is None  # too generic on its own
        assert get("video.mp4", "/v/Gamma (2005)") == ["Western"]
        assert get("video.mp4", "/v/Somewhere") is None  # even when only one Radarr movie has that name

        # The ambiguous and genre-less ones go to TMDB
        service.fill_from_tmdb([leaf("Metamorphosis (2019) copy.mkv"), leaf("Dollhouse.2025.mkv", folder="/v/Dollhouse (2025)")])
        assert tmdb.calls == [("Metamorphosis", 2019), ("Dollhouse", 2025)]

        # An instance that fails keeps its last list, and is retried in 5 minutes, not an hour
        flaky.error = RuntimeError("500")
        service.refresh_radarr(force=True)
        assert get("Ronin.1998.mkv", "/v/Ronin (1998)") == ["Action"]
        assert service._radarr_next - time.time() <= gs.RADARR_RETRY_SECONDS
        service.refresh_radarr()
        assert flaky.calls == 2  # not yet
        service._radarr_next = 0
        flaky.error = None
        service.refresh_radarr()
        assert flaky.calls == 3 and service._radarr_next - time.time() > gs.RADARR_RETRY_SECONDS

        # A response that isn't a list counts as a failure, not as "no movies"
        flaky.movies = {"message": "Unauthorized"}
        service.refresh_radarr(force=True)
        assert get("Ronin.1998.mkv", "/v/Ronin (1998)") == ["Action"]

        # Duplicates in the playlist are listed once; genres differing only in case merge
        service.tmdb_cache["solo|2005"] = {"genres": ["crime"], "at": int(time.time())}
        items = [leaf("Heat.1995.mkv", 0, folder="/v/Heat (1995)"), leaf("Heat.1995.mkv", 1, folder="/v/Heat (1995)"),
                 leaf("Solo.2005.mkv", 2)]
        index = service.by_genre(items)
        assert list(index) == ["Crime"] and [n for n, _ in index["Crime"]] == [1, 3]


def check_tmdb_genres():
    service = TMDBService.__new__(TMDBService)
    service.api_key, service.logger = "key", mock.Mock()
    genres_api = mock.Mock(return_value=mock.Mock(movie_list=mock.Mock(
        return_value={"genres": [{"id": 28, "name": "Action"}, {"id": 80, "name": "Crime"}]})))

    def best(title, release, genre_ids=(80, 28, 999), original=""):
        return mock.Mock(return_value=(90.0, {"title": title, "original_title": original,
                                              "release_date": release, "genre_ids": list(genre_ids)}))

    with mock.patch.object(tmdb_mod.tmdb, "Genres", genres_api):
        service._find_best_movie_result = best("Heat", "1995-12-15")
        assert service.movie_genres("Heat", 1995) == ["Crime", "Action"]  # unknown id dropped
        service.movie_genres("Heat", 1995)
        assert genres_api.call_count == 1  # genre names fetched once
        service._find_best_movie_result = best("Heat", "1996-01-01")
        assert service.movie_genres("Heat", 1995) == ["Crime", "Action"]  # a year off is fine
        # Weak matches give no genres rather than wrong ones
        service._find_best_movie_result = best("Heat", "2013-05-01")
        assert service.movie_genres("Heat", 1995) == []  # wrong year
        service._find_best_movie_result = best("The Heat Is On In Saigon Tonight", "1995-01-01")
        assert service.movie_genres("Heat", 1995) == []  # title only partly matches
        service._find_best_movie_result = best("Kimi no Na wa.", "2016-08-26", original="君の名は。")
        assert service.movie_genres("Kimi no Na wa", 2016) == ["Crime", "Action"]
        service._find_best_movie_result = best("君の名は。", "2016-08-26", original="Your Name.")
        assert service.movie_genres("Your Name", 2016) == ["Crime", "Action"]  # original title counts
        service._find_best_movie_result = mock.Mock(return_value=None)
        assert service.movie_genres("Nothing") == []
        service._find_best_movie_result = mock.Mock(side_effect=RuntimeError("timeout"))
        assert service.movie_genres("Heat") is None
    service.api_key = None
    assert service.movie_genres("Heat") is None


def _interaction(user_id=1, values=None):
    response = types.SimpleNamespace(
        send_message=mock.AsyncMock(), defer=mock.AsyncMock(), edit_message=mock.AsyncMock())
    return types.SimpleNamespace(
        user=types.SimpleNamespace(id=user_id), response=response, channel="chan",
        followup=types.SimpleNamespace(send=mock.AsyncMock()),
        data={"values": values or []}, message=types.SimpleNamespace(edit=mock.AsyncMock()))


def _cog(playback):
    bot = types.SimpleNamespace(get_cog=lambda name: playback if name == "PlaybackCommands" else None)
    return types.SimpleNamespace(bot=bot, vlc=mock.Mock(play_item=mock.Mock(return_value=True)))


def _playback(guard=(True, ""), confirm=True):
    return types.SimpleNamespace(
        _playback_guard=mock.Mock(return_value=guard),
        _confirm_nearby_schedule=mock.AsyncMock(return_value=confirm),
        _seat_playback_owner=mock.Mock(), _announce_now_playing=mock.AsyncMock())


def check_picker():
    async def run():
        results = [(n, leaf(f"Film {n:02d} (2000).mkv", n)) for n in range(1, 46)]

        # 45 movies: 3 pages of 20, the select showing the current page
        playback = _playback()
        view = pl.GenrePickerView(_cog(playback), 1, "Drama", results)
        select = next(c for c in view.children if isinstance(c, discord.ui.Select))
        assert view.pages == 3 and [o.value for o in select.options] == [str(n) for n in range(1, 21)]
        embed = view.build_embed()
        assert embed.title == "🎭 Drama" and "45 movies · page 1/3" in embed.footer.text
        assert embed.description.count("\n") == 19 and len(embed.description) < 4096

        previous, following = [c for c in view.children if isinstance(c, discord.ui.Button) and c.label in "◀️▶️"]
        await following.callback(_interaction())
        assert [o.value for o in select.options][0] == "21" and "page 2/3" in view.build_embed().footer.text
        await previous.callback(_interaction())
        await previous.callback(_interaction())  # wraps to the last page
        assert [o.value for o in select.options] == [str(n) for n in range(41, 46)]
        stranger = _interaction(user_id=2)
        await following.callback(stranger)
        assert view.page == 2 and stranger.response.send_message.call_args.kwargs["ephemeral"]

        # Picking: guard, then the schedule check on the picked item, then play
        pick = _interaction(values=["43"])
        await select.callback(pick)
        playback._confirm_nearby_schedule.assert_awaited_once_with("chan", pick.user, results[42][1])
        pick.response.defer.assert_awaited_once()
        view.cog.vlc.play_item.assert_called_once_with("143")
        assert pick.followup.send.call_args.args[0].startswith("Loading item #43")

        # Cancelling the schedule prompt plays nothing
        playback = _playback(confirm=False)
        view = pl.GenrePickerView(_cog(playback), 1, "Drama", results[:3])
        assert not [c for c in view.children if isinstance(c, discord.ui.Button) and c.label in "◀️▶️"]
        await next(c for c in view.children if isinstance(c, discord.ui.Select)).callback(_interaction(values=["2"]))
        view.cog.vlc.play_item.assert_not_called()

        # Outside the room: refused before any prompt
        playback = _playback(guard=(False, "You must be in **Room**"))
        view = pl.PlaySearchView(_cog(playback), 1, results[:3])
        denied = _interaction(values=["2"])
        await view.children[0].callback(denied)
        playback._confirm_nearby_schedule.assert_not_awaited()
        assert "must be in" in denied.response.send_message.call_args.args[0]

    asyncio.run(run())


def check_genre_reply():
    async def run():
        cog = pl.PlaylistCommands.__new__(pl.PlaylistCommands)
        cog.genres = mock.Mock()
        cog._genre_index_cache = (0.0, {})
        cog._get_playlist_items = lambda: []
        cog.genres.by_genre.return_value = {}
        assert "load in the background" in (await cog.genre_reply(1, "drama"))["content"]

        # An empty index (still loading) isn't reused: the next request looks again
        drama = [(1, leaf("A (2000).mkv"))]
        cog.genres.by_genre.return_value = {"Drama": drama, "Action": drama * 2, "Animation": drama}
        listing = (await cog.genre_reply(1, ""))["content"]
        assert listing.endswith("Action (2) · Animation (1) · Drama (1)")
        assert "No single genre matches *a*" in (await cog.genre_reply(1, "a"))["content"]
        assert "*\\*\\*bold\\*\\**" in (await cog.genre_reply(1, "**bold**"))["content"]  # escaped
        reply = await cog.genre_reply(1, "dra")
        assert reply["embed"].title == "🎭 Drama" and isinstance(reply["view"], pl.GenrePickerView)
        assert cog.genres.by_genre.call_count == 2  # empty once, then built and reused within the minute

        # However many genres, the listing fits one Discord message
        cog._genre_index_cache = (0.0, {})
        cog.genres.by_genre.return_value = {f"Genre number {n:03d}": drama for n in range(300)}
        listing = (await cog.genre_reply(1, ""))["content"]
        assert len(listing) <= 2000 and listing.endswith(" · …") and "Genre number 000 (1)" in listing

        # Names with markdown don't break the list
        view = pl.GenrePickerView(None, 1, "Drama", [(1, leaf("Film *Star* (2000).mkv"))])
        assert "Film \\*Star\\* (2000)" in view.build_embed().description

    asyncio.run(run())


def main():
    check_parsing()
    check_service()
    check_radarr_edge_cases()
    check_tmdb_genres()
    check_picker()
    check_genre_reply()
    print("ok")


if __name__ == "__main__":
    main()
