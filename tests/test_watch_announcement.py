"""Runnable self-check for the watch-folder "New Media Added" announcement embed.

Run from the repo root:  python tests/test_watch_announcement.py

bot.py runs Config.validate() at import time, so the builder and its helpers are
extracted via ast (like test_config_overview_display.py) and run against fake
TMDB/VLC services.
"""
import ast
import logging
import os
import pathlib
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import discord  # noqa: E402
from src.utils.media_utils import MediaUtils  # noqa: E402

HELPERS = {'_format_bytes', '_detect_season', '_announce_list_line', '_lookup_season_embed',
           '_lookup_single_embed', '_media_added_embed', '_add_playlist_positions',
           '_build_watch_announcement_embed'}


class FakeTMDB:
    def __init__(self, movies=None, tv=None):
        self.movies, self.tv, self.calls, self._last_match_score = movies or {}, tv or {}, [], 0.0

    def get_movie_metadata(self, title, year=None, file_path=None):
        self.calls.append(('movie', title))
        hit = self.movies.get(title)
        self._last_match_score = hit[1] if hit else 0.0
        return discord.Embed(title=title, description=hit[0]) if hit else None

    def get_tv_metadata(self, title, season=None, year=None):
        self.calls.append(('tv', title))
        hit = self.tv.get(title)
        self._last_match_score = hit[1] if hit else 0.0
        return discord.Embed(title=title, description=hit[0]) if hit else None


class FakeVLC:
    def __init__(self, paths):
        self.paths = paths

    def get_playlist(self):
        root = ET.Element('node')
        for p in self.paths:
            ET.SubElement(root, 'leaf', uri=pathlib.Path(p).as_uri())
        return root

    def _uri_to_path(self, uri):
        from urllib.parse import unquote
        return unquote(uri[7:])


def build(paths, tmdb, is_initial=False, suppress=True, kofi=None):
    src = (ROOT / 'bot.py').read_text()
    ns = dict(
        discord=discord, os=os, re=re, logger=logging.getLogger('test'), MediaUtils=MediaUtils,
        tmdb_service=tmdb, vlc=FakeVLC(paths),
        Config=SimpleNamespace(KOFI_URL=kofi, SUPPRESS_SINGLE_TV=suppress,
                               DISCORD_COMMAND_PREFIX='!', ENABLE_PREFIX_COMMANDS=True),
    )
    for n in ast.parse(src).body:
        if isinstance(n, ast.FunctionDef) and n.name in HELPERS:
            exec(ast.get_source_segment(src, n), ns)
    embed = ns['_build_watch_announcement_embed'](paths, is_initial, 3)
    return embed.to_dict() if embed else None


def fields(embed):
    return [f['name'] for f in embed.get('fields', [])]


def main():
    tmp = pathlib.Path(tempfile.mkdtemp())

    def mk(rel):
        p = tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b'x' * 1024)
        return str(p)

    season = [mk(f'TV/Some Show/Season 2/Some.Show.S02E0{i}.mkv') for i in range(1, 6)]
    movie = mk('Movies/Dune Part Two (2024) {edition-IMAX}/Dune.Part.Two.2024.{edition-IMAX}.mkv')
    ambiguous = mk('Movies/Fargo (1996)/Fargo.1996.mkv')
    episode = mk('TV/Fargo/Season 1/Fargo.S01E03.mkv')
    tv_db = {'Some Show': ('A show.', 90.0), 'Fargo': ('TV Fargo.', 80.0)}
    movie_db = {'Dune Part Two': ('Paul goes to war.', 95.0), 'Fargo': ('Movie Fargo.', 70.0)}

    # Batch with a season match -> TMDB season embed named after the folder above "Season 2"
    tmdb = FakeTMDB(movie_db, tv_db)
    e = build(season, tmdb)
    assert e['title'] == '✨ New Media Added: Some Show' and tmdb.calls == [('tv', 'Some Show')]
    assert e['description'].startswith('A show.\n\n• ') and e['description'].endswith('… and 2 more')
    assert fields(e) == ['Total Size', 'Playlist Positions']

    # Batch without a match -> plain list, Total Size exactly once, Ko-fi when configured
    e = build(season, FakeTMDB(), kofi='https://ko-fi.com/x')
    assert e['title'] == '✨ New Media Added'
    assert fields(e) == ['Total Size', 'Support kutsaratinidor by supporting CtrlVee', 'Playlist Positions']

    # Initial scan -> no TMDB lookups at all
    tmdb = FakeTMDB(movie_db, tv_db)
    assert fields(build(season, tmdb, is_initial=True)) == ['Total Size', 'Playlist Positions']
    e = build([movie], tmdb, is_initial=True)
    assert tmdb.calls == [] and fields(e) == ['File Size', 'Edition', 'Playback']

    # Single movie -> one movie + one TV lookup (no duplicate movie lookup), Edition shown for the movie match
    tmdb = FakeTMDB(movie_db, tv_db)
    e = build([movie], tmdb)
    assert [c[0] for c in tmdb.calls] == ['movie', 'tv'], tmdb.calls
    assert e['title'] == '✨ New Media Added: Dune Part Two' and fields(e) == ['Edition', 'File Size', 'Playback']

    # No episode marker -> the better-scoring of movie vs TV wins
    assert build([ambiguous], FakeTMDB(movie_db, tv_db))['description'].endswith('TV Fargo.')
    assert build([ambiguous], FakeTMDB(movie_db, {}))['description'].endswith('Movie Fargo.')

    # Explicit single episode -> suppressed before any lookup; sent as TV when suppression is off
    tmdb = FakeTMDB(movie_db, tv_db)
    assert build([episode], tmdb) is None and tmdb.calls == []
    assert build([episode], FakeTMDB(movie_db, tv_db), suppress=False)['title'] == '✨ New Media Added: Fargo'

    # No TMDB configured -> plain list embed
    assert fields(build([movie], None)) == ['File Size', 'Edition', 'Playback']

    print(f"OK  ({os.path.basename(__file__)}): all watch announcement cases pass")


if __name__ == "__main__":
    main()
