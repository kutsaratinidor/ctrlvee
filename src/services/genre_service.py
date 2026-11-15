"""Genres for the movies in the VLC playlist, for browsing the playlist by genre.

Radarr is the main source: one request per instance returns every movie with its
genres and file. Playlist items are matched to it by folder + file name, then file
name, then title + year. TMDB fills in movies Radarr doesn't have, a few at a time
in the background, and its answers are cached on disk so each is looked up once.

Anything ambiguous (two Radarr movies with the same file name or title + year but
different genres) is treated as unknown rather than guessed, and falls through to
the next way of matching, ending at TMDB.
"""
import json
import logging
import os
import re
import time
import unicodedata
from functools import lru_cache
from urllib.parse import unquote, urlparse

from ..utils.media_utils import MediaUtils

logger = logging.getLogger(__name__)

RADARR_REFRESH_SECONDS = 3600
RADARR_RETRY_SECONDS = 300  # after an instance failed
TMDB_LOOKUPS_PER_RUN = 20
TMDB_NO_MATCH_RETRY_SECONDS = 30 * 86400  # TMDB may add a movie later

# Everything else counts as video, so an unusual video extension isn't left out
NOT_VIDEO = {
    ".mp3", ".flac", ".aac", ".wav", ".ogg", ".m4a", ".opus", ".wma", ".alac",
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp",
    ".srt", ".ass", ".ssa", ".sub", ".idx", ".vtt",
    ".m3u", ".m3u8", ".xspf", ".pls", ".nfo", ".txt",
}


@lru_cache(maxsize=100_000)  # these all depend only on a path or name, which don't change
def _fold(text: str) -> str:
    """Case- and accent-insensitive form: "Léon" and "Leon" (and macOS's decomposed
    "Léon") all become "leon"."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()


@lru_cache(maxsize=100_000)
def _local_path(path: str | None) -> str | None:
    """A filesystem path from a file URI, a plain path, or a Radarr path (Linux or Windows).
    None for streams and other non-file URIs."""
    if not path:
        return None
    scheme = urlparse(path).scheme.lower()
    if scheme and scheme != "file" and len(scheme) > 1:  # one letter is a Windows drive
        return None
    if scheme == "file":
        path = unquote(path[len("file://"):])
    return unicodedata.normalize("NFC", path.replace("\\", "/"))


def _item_path(item) -> str | None:
    uri = item.get("uri")
    return _local_path(uri) if uri else _local_path(item.get("name"))


@lru_cache(maxsize=100_000)
def _file_keys(path: str) -> tuple[str, str]:
    """(folder/file, file) lookup keys. The folder is compared too where it can be, so
    generic names like "movie.mkv" in different folders aren't mixed up."""
    parts = [p for p in path.split("/") if p]
    file_key = _fold(parts[-1]) if parts else ""
    folder_key = _fold("/".join(parts[-2:])) if len(parts) > 1 else ""
    return folder_key, file_key


@lru_cache(maxsize=50_000)  # filenames don't change, and parsing every playlist item is the slow part
def title_key(name: str) -> str | None:
    """"title|year" from a filename or "Title (Year)", the same for Radarr and playlist files.
    The year part is empty when there isn't one."""
    title, year = MediaUtils.parse_movie_filename(unicodedata.normalize("NFC", name or ""))
    norm = re.sub(r"\W+", " ", _fold(title or "")).strip()
    return f"{norm}|{year or ''}" if norm else None


@lru_cache(maxsize=50_000)
def _is_movie_file(name: str) -> bool:
    # parse_tv_filename finds S01E02 / 1x02 without mistaking "DD+5.1x264" or "1920x804" for one
    if not name or os.path.splitext(name)[1].lower() in NOT_VIDEO:
        return False
    return not MediaUtils.parse_tv_filename(name)[2]


@lru_cache(maxsize=50_000)
def _sort_name(name: str) -> str:
    return _fold(MediaUtils.clean_filename_for_display(name))


def is_movie(item) -> bool:
    """A local video file that isn't a TV episode: the genre list is movies only."""
    path = _item_path(item)
    return bool(path) and _is_movie_file(os.path.basename(path))


def _clean_genres(raw) -> list:
    """Genre names from Radarr (strings, or {"name": ...} in some versions), trimmed and
    without repeats."""
    seen, genres = set(), []
    for genre in raw if isinstance(raw, list) else []:
        name = genre if isinstance(genre, str) else genre.get("name") if isinstance(genre, dict) else None
        name = name.strip() if isinstance(name, str) else ""
        if name and name.casefold() not in seen:
            seen.add(name.casefold())
            genres.append(name)
    return genres


def _put(index: dict, key: str | None, genres: list) -> None:
    """Record genres under a key; a key two movies share with different genres becomes
    None, which lookups treat as unknown."""
    if not key:
        return
    if key in index and (index[key] is None or sorted(index[key]) != sorted(genres)):
        index[key] = None
    else:
        index.setdefault(key, genres)


class GenreService:
    def __init__(self, radarr_services=(), tmdb=None, path: str = "genre_cache.json"):
        self.radarr = [svc for svc in radarr_services if svc.is_configured()]
        self.tmdb = tmdb
        self.path = path
        # title key -> {"genres": [...], "at": unix time}; empty genres means TMDB had no match
        self.tmdb_cache = self._load()
        self._radarr_lists: dict[int, list] = {}  # last good movie list per instance
        self._by_folder: dict = {}
        self._by_file: dict = {}
        self._by_title: dict = {}
        self._radarr_next = 0.0

    def _load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return {}
        except ValueError as e:
            logger.error(f"Unreadable {self.path}, starting a new genre cache: {e}")
            return {}
        if not isinstance(data, dict):
            return {}
        return {k: v for k, v in data.items() if isinstance(v, dict) and isinstance(v.get("genres"), list)}

    def _save(self) -> None:
        tmp_path = f"{self.path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(self.tmdb_cache, f)
        os.replace(tmp_path, self.path)

    def refresh_radarr(self, force: bool = False) -> None:
        """Reload every Radarr instance's movie list (blocking): hourly, or after 5 minutes
        when an instance failed. A failed instance keeps its last good list meanwhile."""
        if not self.radarr or (not force and time.time() < self._radarr_next):
            return
        failed = 0
        for n, svc in enumerate(self.radarr):
            try:
                movies = svc.list_movies()
                if not isinstance(movies, list):
                    raise ValueError(f"expected a list of movies, got {type(movies).__name__}")
                self._radarr_lists[n] = movies
            except Exception as e:
                failed += 1
                kept = len(self._radarr_lists.get(n, []))
                logger.warning(f"Genre refresh: Radarr at {svc.host} failed ({e}); keeping its last {kept} movies")
        self._radarr_next = time.time() + (RADARR_RETRY_SECONDS if failed else RADARR_REFRESH_SECONDS)

        by_folder, by_file, by_title = {}, {}, {}
        for movies in self._radarr_lists.values():
            for movie in movies:
                if not isinstance(movie, dict):
                    continue
                genres = _clean_genres(movie.get("genres"))
                if not genres:  # unknown to Radarr: leave it to TMDB
                    continue
                movie_file = movie.get("movieFile") if isinstance(movie.get("movieFile"), dict) else {}
                path = movie_file.get("path")
                if not path and movie_file.get("relativePath") and movie.get("path"):
                    path = f"{movie['path']}/{movie_file['relativePath']}"
                local = _local_path(path)
                if local:
                    folder_key, file_key = _file_keys(local)
                    _put(by_folder, folder_key, genres)
                    _put(by_file, file_key, genres)
                # By title only with a year: a yearless title (an announced movie) would match
                # any playlist file without a year in its name
                if movie.get("year"):
                    for title in {movie.get("title"), movie.get("originalTitle")} - {None, ""}:
                        _put(by_title, title_key(f"{title} ({movie['year']})"), genres)
        self._by_folder, self._by_file, self._by_title = by_folder, by_file, by_title
        logger.info(
            f"Genre refresh: {sum(g is not None for g in by_file.values())} movie files from "
            f"{len(self.radarr) - failed}/{len(self.radarr)} Radarr instance(s)"
        )

    def _radarr_genres(self, path: str) -> list | None:
        folder_key, file_key = _file_keys(path)
        if self._by_folder.get(folder_key):
            return self._by_folder[folder_key]
        key = title_key(os.path.basename(path))
        # A file name without a year ("movie.mkv", "video.mp4") is too generic to match
        # on its own; with one, it's as specific as the title match below
        if not key or key.endswith("|"):
            return None
        return self._by_file.get(file_key) or self._by_title.get(key)

    def genres_for(self, item) -> list | None:
        """Genres of a playlist movie: None when it isn't a movie or isn't known yet,
        [] when TMDB found no match."""
        if not is_movie(item):
            return None
        path = _item_path(item)
        genres = self._radarr_genres(path)
        if genres is not None:
            return genres
        entry = self.tmdb_cache.get(title_key(os.path.basename(path)))
        return entry["genres"] if entry else None

    def fill_from_tmdb(self, items, limit: int = TMDB_LOOKUPS_PER_RUN) -> int:
        """Look up to `limit` playlist movies Radarr doesn't know on TMDB (blocking).
        Returns how many were tried."""
        if not self.tmdb:
            return 0
        now, tried = time.time(), 0
        for item in items:
            if tried >= limit:
                break
            if not is_movie(item):
                continue
            path = _item_path(item)
            if self._radarr_genres(path) is not None:
                continue
            name = os.path.basename(path)
            key = title_key(name)
            entry = self.tmdb_cache.get(key)
            if not key or (entry and (entry["genres"] or now - entry.get("at", 0) < TMDB_NO_MATCH_RETRY_SECONDS)):
                continue
            title, year = MediaUtils.parse_movie_filename(name)
            genres = self.tmdb.movie_genres(title, year)
            tried += 1
            if genres is not None:  # None is an error: try again next run
                self.tmdb_cache[key] = {"genres": genres, "at": int(now)}
        if tried:
            self._save()
        return tried

    def by_genre(self, items) -> dict[str, list]:
        """{genre: [(1-based playlist number, item), ...]} for the movies with known genres,
        each sorted by title. A file in the playlist twice is listed once, and genres that
        differ only in case are merged."""
        index: dict[str, list] = {}
        names: dict[str, str] = {}
        seen: set[str] = set()
        for number, item in enumerate(items, 1):
            genres = self.genres_for(item)
            if not genres:
                continue
            path_key = _fold(_item_path(item))
            if path_key in seen:
                continue
            seen.add(path_key)
            for genre in genres:
                name = names.setdefault(genre.casefold(), genre)
                index.setdefault(name, []).append((number, item))
        for entries in index.values():
            entries.sort(key=lambda e: _sort_name(e[1].get("name") or ""))
        return index


def match_genre(query: str, genres) -> str | None:
    """The genre a user meant: exact (any case), else the only one starting with or containing it."""
    q = _fold(query or "").strip()
    if not q:
        return None
    genres = list(genres)
    for test in (lambda g: _fold(g) == q, lambda g: _fold(g).startswith(q), lambda g: q in _fold(g)):
        found = [g for g in genres if test(g)]
        if len(found) == 1:
            return found[0]
    return None
