"""Runnable self-check for the schedule-proximity guard's pure lookup.

Run from the repo root:  python tests/test_schedule_proximity_guard.py
Fails loudly if the "is an upcoming scheduled movie close to now" logic regresses.
"""
import sys
import os
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.cogs.playback import _find_nearby_schedule  # noqa: E402

NOW = datetime(2026, 1, 1, 21, 0, tzinfo=timezone.utc)


def _entry(number, minutes_from_now, title="Movie", user=1):
    return {
        "number": number,
        "title": title,
        "user": user,
        "dt": NOW + timedelta(minutes=minutes_from_now),
    }


def main():
    # No schedules -> nothing nearby
    assert _find_nearby_schedule([], NOW, 1800) is None

    # A schedule far outside the window is ignored
    far = [_entry(1, minutes_from_now=90)]
    assert _find_nearby_schedule(far, NOW, 1800) is None

    # A schedule just inside the window (30 min = 1800s) is found
    just_inside = [_entry(2, minutes_from_now=25)]
    result = _find_nearby_schedule(just_inside, NOW, 1800)
    assert result is not None and result["number"] == 2

    # A schedule that has already started does NOT trigger — only upcoming ones count
    already_started = [_entry(3, minutes_from_now=-5)]
    assert _find_nearby_schedule(already_started, NOW, 1800) is None

    # Exactly "now" (0s away) is inclusive — about to start counts as upcoming
    right_now = [_entry(4, minutes_from_now=0)]
    assert _find_nearby_schedule(right_now, NOW, 1800) is not None

    # Exactly at the window edge (1800s) is inclusive
    edge = [_entry(5, minutes_from_now=30)]
    assert _find_nearby_schedule(edge, NOW, 1800) is not None

    # Multiple upcoming candidates -> the soonest one wins
    multiple = [_entry(6, minutes_from_now=20), _entry(7, minutes_from_now=5)]
    result = _find_nearby_schedule(multiple, NOW, 1800)
    assert result is not None and result["number"] == 7, (
        f"expected the soonest schedule (#7) to win, got {result}"
    )

    # A past schedule and an upcoming one -> only the upcoming one is ever a candidate
    mixed = [_entry(8, minutes_from_now=-2), _entry(9, minutes_from_now=10)]
    result = _find_nearby_schedule(mixed, NOW, 1800)
    assert result is not None and result["number"] == 9

    # A malformed entry (no 'dt') is skipped rather than raising
    malformed = [{"number": 10, "title": "Bad"}, _entry(11, minutes_from_now=1)]
    result = _find_nearby_schedule(malformed, NOW, 1800)
    assert result is not None and result["number"] == 11

    print(f"OK  ({os.path.basename(__file__)}): all schedule-proximity guard cases pass")


if __name__ == "__main__":
    main()
