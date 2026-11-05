"""Runnable self-check for show-config / role-denial display helpers.

Run from the repo root:  python tests/test_config_overview_display.py

bot.py runs Config.validate() at import time, which fails offline (e.g. when
the media share is unmounted), so these helpers are extracted via ast rather
than importing bot.py — that keeps them testing the real source.
"""
import ast
import os
import pathlib
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load_func(name):
    src = (ROOT / 'bot.py').read_text()
    tree = ast.parse(src)
    fn = next(
        n for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name
    )
    ns = {}
    exec(ast.get_source_segment(src, fn), ns)
    return ns[name], ns


class FakeRole:
    def __init__(self, role_id, name):
        self.id = role_id
        self.name = name


class FakeGuild:
    def __init__(self, roles):
        self.roles = roles

    def get_role(self, role_id):
        return next((r for r in self.roles if r.id == role_id), None)


def _guild_with(roles):
    return FakeGuild([FakeRole(rid, name) for rid, name in roles])


def _check_clip(clip):
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

    for sample in ([f'p{i}' * 200 for i in range(20)], ['q' * 1000]):
        assert len(clip(sample)) <= 1000


def _check_roles_display(func, module_ns):
    module_ns['Config'] = SimpleNamespace()
    cfg = module_ns['Config']

    cfg.ALLOWED_ROLES = ['CtrlVee admin', 1540229242439213056]
    guild = _guild_with([(1540229242439213056, 'CtrlVee admin')])
    assert func(guild) == 'CtrlVee admin', \
        f"ID resolves to its name and dedupes the duplicate name: {func(guild)!r}"

    guild = _guild_with([(1540229242439213056, 'Theater Host')])
    assert func(guild) == 'CtrlVee admin, Theater Host', \
        f"resolved ID name and configured name both shown: {func(guild)!r}"

    assert func(_guild_with([])) == 'CtrlVee admin, ID:1540229242439213056', \
        "unresolved ID falls back to the raw ID"

    assert func(None) == 'CtrlVee admin, ID:1540229242439213056', \
        "no guild -> ID fallback"

    cfg.ALLOWED_ROLES = ['Theater 2', 'theater 2', 'Theater Host']
    assert func(_guild_with([])) == 'Theater 2, Theater Host', \
        "duplicate names collapse case-insensitively"

    cfg.ALLOWED_ROLES = []
    assert func(_guild_with([])) == '', "empty config -> empty text"


def main():
    clip, _ = _load_func('_clip_field_lines')
    _check_clip(clip)

    roles_display, ns = _load_func('_format_allowed_roles_for_display')
    _check_roles_display(roles_display, ns)

    print(f"OK  ({os.path.basename(__file__)}): field clipper and role display pass")


if __name__ == "__main__":
    main()