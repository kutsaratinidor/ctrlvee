"""Runnable self-check: the current version's CHANGELOG entry yields a short startup summary.

Run from the repo root:  python tests/test_whats_new.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from changelog_helper import whats_new  # noqa: E402
from src.version import __version__  # noqa: E402

items = whats_new(__version__)
assert items, f"CHANGELOG.md has no **bold** Added/Changed bullets for {__version__}"
assert all("**" not in i["title"] and not i["title"].endswith(":") for i in items), items
assert whats_new("0.0.0") == []
assert len(whats_new("1.26.0", max_items=1)) == 1

roulette = whats_new("1.27.0")[0]
assert roulette == {"title": "Movie roulette", "slash": "/playlist roulette", "prefix": "!roulette"}, roulette
changed = [i for i in whats_new("1.26.0") if i["title"] == "Schedule list as poster cards"][0]
assert changed["slash"] is None and changed["prefix"] is None  # only Added bullets get a command hint
print("whats new ok:", items)
