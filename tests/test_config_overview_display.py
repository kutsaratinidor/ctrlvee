"""Runnable self-check for the show-config embed field clipper.

Run from the repo root:  python tests/test_config_overview_display.py

bot.py runs Config.validate() at import time, which fails offline (e.g. when
the media share is unmounted), so this extracts _clip_field_lines' source via
ast instead of importing bot.py — that keeps it testing the real function.
"""
import ast
import os
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load_clip_field_lines():
    src = (ROOT / 'bot.py').read_text()
    tree = ast.parse(src)
    fn = next(
        n for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name == '_clip_field_lines'
    )
    ns = {}
    exec(ast.get_source_segment(src, fn), ns)
    return ns['_clip_field_lines']


def main():
    clip = _load_clip_field_lines()

    assert clip(['a', 'bb', 'ccc']) == 'a\nbb\nccc', "whole lines preserved when they fit"

    long = ['x' * 300, 'y' * 300, 'z' * 300, 'w' * 300]
    out = clip(long)
    assert len(out) <= 1000 and '… and 1 more' in out and out.startswith('x' * 300), "drops tail lines with count"

    out = clip(['z' * 2000])
    assert len(out) <= 1000 and out.endswith('…'), "single over-long line hard-truncated"

    out = clip(['z' * 2000, 'short', 'shorter'])
    assert '… and 2 more' in out and len(out) <= 1000, "over-long first line + rest"

    out = clip(['a' * 990, 'b' * 300, 'c' * 300])
    assert len(out) <= 1000 and '… and 2 more' in out, "suffix budgeted inside the cap"

    assert clip([]) == '', "empty input -> empty output"

    # Every output must stay under Discord's 1024-char field cap.
    for sample in ([f'p{i}' * 200 for i in range(20)], ['q' * 1000]):
        assert len(clip(sample)) <= 1000

    print(f"OK  ({os.path.basename(__file__)}): field clipper stays under the embed cap")


if __name__ == "__main__":
    main()