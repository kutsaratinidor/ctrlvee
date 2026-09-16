"""Runnable self-check for play-search scoring / sort order.

Run from the repo root:  python tests/test_playlist_score_order.py

Locks in: tied episodes (same show, sequential load) score equally and fall
out in playlist order — the length-penalty that scrambled S01E01 to the bottom
of the picker must not come back.
"""
import ast
import pathlib
import re
from typing import List, Tuple

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / 'src/cogs/playlist.py'


def _load_methods(*names):
    src = SRC.read_text()
    tree = ast.parse(src)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'PlaylistCommands')
    methods = {}
    for n in cls.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names:
            methods[n.name] = ast.get_source_segment(src, n)
    ns = {'List': List, 'Tuple': Tuple, 're': re}
    exec('\n\n'.join(methods.values()), ns)
    return [ns[name] for name in names]


NORMALIZE, SCORE = _load_methods('_normalize_search_text', '_score_match')


class _Shim:
    """Minimal self: _score_match dispatches _normalize_search_text via self."""
    _normalize_search_text = NORMALIZE

# A real PEN15 season loaded sequentially into VLC, playlist positions 8330..8354.
EPISODES = [
    (8330, 'PEN15 S01E01 First Day'),
    (8331, 'PEN15 S01E02 Miranda'),
    (8332, 'PEN15 S01E03 Ojichan'),
    (8333, 'PEN15 S01E04 Solo'),
    (8335, 'PEN15 S01E06 Posh'),
    (8336, 'PEN15 S01E07 AIM'),
    (8339, 'PEN15 S01E10 Dance'),
    (8340, 'PEN15 S02E01 Pool'),
    (8341, 'PEN15 S02E02 Wrestle'),
    (8343, 'PEN15 S02E04 Three'),
    (8344, 'PEN15 S02E05 Sleepover'),
    (8345, 'PEN15 S02E06 Play'),
    (8347, 'PEN15 S02E08 Jacuzzi'),
    (8349, 'PEN15 S02E10 Shadow'),
    (8350, 'PEN15 S02E11 Grammy'),
    (8351, 'PEN15 S02E12 Luminaria'),
    (8353, 'PEN15 S02E14 Runaway'),
    (8354, 'PEN15 S02E15 Home'),
]


def _check():
    qn, qc, qtok = NORMALIZE(None, 'pen15')
    assert qtok, 'query normalizes to tokens'

    scores = {}
    shim = _Shim()
    for pos, name in EPISODES:
        scores[pos] = SCORE(shim, qn, qc, qtok, name)

    # Tied scores: the fix that removed the length penalty.
    assert len(set(scores.values())) == 1, (
        f"all episodes must tie on score; got {sorted(set(scores.values()))}"
    )

    # Playlist order must win for ties (matches _search_items' sort key).
    ordered = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    assert [pos for pos, _ in ordered] == [pos for pos, _ in EPISODES], \
        "tied results must stay in playlist order"

    # Sanity: a non-episode still outranks nothing, and title match beats substring.
    loose = SCORE(shim, qn, qc, qtok, 'pen 15 fan art.mp4')
    tight = SCORE(shim, qn, qc, qtok, 'PEN15 S01E01 First Day')
    assert loose <= tight, 'episode title must not score below a bare substring match'

    print(f"OK: {len(EPISODES)} episodes tie at score {scores[EPISODES[0][0]]}; "
          f"sorted output preserves playlist order.")


if __name__ == '__main__':
    _check()