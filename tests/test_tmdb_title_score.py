"""Runnable self-check for TMDB title scoring (no network).

Run from the repo root:  python tests/test_tmdb_title_score.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.services.tmdb_service import TMDBService

svc = TMDBService.__new__(TMDBService)


def score(search, title, year=2023, item_year=2023):
    return svc._compute_title_score(search, title, '', year, item_year, 0.0, 0)


# '&' and 'and' are the same title
assert score("Love and Death", "Love & Death") == score("Love & Death", "Love & Death") == 150.0
# '&' no longer collapses into a different title ("lovedeath" in "lovedeathdog")
assert score("Love & Death", "Love & Death") > score("Love & Death", "Love Death Dog") + 50
# A short title buried in a long one is a weak partial, not a near-exact match
assert score("Love and Death", "10 Stories About Love and Death", item_year=2026) < score("Love and Death", "Love & Death")
assert score("Love and Death", "10 Stories About Love and Death", item_year=2023) < 100
# Near-exact partials still score high
assert score("Dune Part Two", "Dune: Part Two") == 150.0
print("ok")
