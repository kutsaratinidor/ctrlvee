"""Runnable self-check for TV requests (Overseerr payload, duplicate check, TMDB search).

Run from the repo root:  python tests/test_tv_request.py
"""
import asyncio
import os
import sys
import tempfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import Config  # noqa: E402
from src.services import overseerr_service as svc  # noqa: E402
from src.services import tmdb_service as tmdb_mod  # noqa: E402
from src.services.movie_request_tracker import MovieRequestTracker  # noqa: E402


def _payload(media_type):
    service = svc.OverseerrService("http://seerr", "key")
    response = mock.Mock(status_code=201)
    response.json.return_value = {"id": 7, "media": {"id": 8}}
    with mock.patch.object(svc.requests, "post", return_value=response) as post:
        result = asyncio.run(service.request_media(42, media_type))
    assert result == {"success": True, "request_id": 7, "media_id": 8}
    return post.call_args.kwargs["json"]


def main():
    # Each type goes to its own server; TV always names its seasons
    with mock.patch.object(Config, "OVERSEERR_RADARR_SERVER_ID", 1), mock.patch.object(Config, "OVERSEERR_SONARR_SERVER_ID", 2):
        assert _payload("tv") == {"mediaType": "tv", "mediaId": 42, "seasons": "all", "serverId": 2}
        assert _payload("movie") == {"mediaType": "movie", "mediaId": 42, "serverId": 1}
    # Server id 0 is a real Overseerr id; blank leaves the choice to Overseerr
    with mock.patch.object(Config, "OVERSEERR_SONARR_SERVER_ID", 0):
        assert _payload("tv")["serverId"] == 0
    with mock.patch.object(Config, "OVERSEERR_SONARR_SERVER_ID", None):
        assert "serverId" not in _payload("tv")

    # Every season taken (HTTP 202): a plain answer, not a failure with a status code
    service = svc.OverseerrService("http://seerr", "key")
    response = mock.Mock(status_code=202)
    response.json.return_value = {"message": "No seasons available to request"}
    with mock.patch.object(svc.requests, "post", return_value=response):
        result = asyncio.run(service.request_media(42, "tv"))
    assert result["success"] is False and result["nothing_to_request"] is True
    assert "HTTP" not in result["error"]

    with tempfile.TemporaryDirectory() as tmp:
        tracker = MovieRequestTracker(None, store_file=os.path.join(tmp, "r.json"))
        # Old records have no media_type and count as movies
        tracker.add_request({"tmdb_id": 42, "status": "pending"})
        assert tracker.find_blocking_request(42, "movie")
        # The same TMDB id can be an unrelated series
        assert tracker.find_blocking_request(42, "tv") is None

        tracker.add_request({"tmdb_id": 42, "media_type": "tv", "status": "pending"})
        assert tracker.find_blocking_request(42, "tv")

        # An available series can be requested again for new seasons; a movie can't
        tracker.add_request({"tmdb_id": 43, "media_type": "tv", "status": "available"})
        tracker.add_request({"tmdb_id": 43, "status": "available"})
        assert tracker.find_blocking_request(43, "tv") is None
        assert tracker.find_blocking_request(43, "movie")

        # The latest record decides: a live retry blocks despite the old declined one
        tracker.add_request({"tmdb_id": 44, "status": "declined"})
        assert tracker.find_blocking_request(44) is None
        tracker.add_request({"tmdb_id": 44, "status": "pending"})
        assert tracker.find_blocking_request(44)

    results = {"results": [{"id": 1, "name": "Dark", "first_air_date": "2017-12-01", "overview": "o", "poster_path": "/p"}, {"id": 2, "original_name": "Ohne", "first_air_date": ""}, {"id": 3}]}
    service = tmdb_mod.TMDBService.__new__(tmdb_mod.TMDBService)
    service.api_key = "key"
    service.logger = mock.Mock()
    with mock.patch.object(tmdb_mod.tmdb, "Search") as search:
        search.return_value.tv.return_value = results
        found = service.search_tv("dark")
    assert found[0] == {"tmdb_id": 1, "title": "Dark", "year": 2017, "overview": "o", "poster_path": "/p"}
    assert (found[1]["title"], found[1]["year"]) == ("Ohne", None)
    assert found[2]["title"] == "Untitled"
    with mock.patch.object(tmdb_mod.tmdb, "Search") as search:
        search.return_value.movie.return_value = {"results": [{"id": 9, "title": "Heat", "release_date": "1995-12-15"}]}
        assert service.search_movies("heat")[0]["title"] == "Heat"
    print("ok")


if __name__ == "__main__":
    main()
