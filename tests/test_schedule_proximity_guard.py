"""Runnable self-check for the schedule-proximity guard's pure lookup.

Run from the repo root:  python tests/test_schedule_proximity_guard.py
Fails loudly if the "is an upcoming scheduled movie close to now" logic regresses.
"""
import sys
import os
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import xml.etree.ElementTree as ET  # noqa: E402

from src.cogs.playback import _find_nearby_schedule, _schedule_overlap_window  # noqa: E402
from src.config import Config  # noqa: E402

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

    # Known runtime -> window is exactly how long the target plays (plus any wait before it)
    movie = ET.fromstring('<leaf id="1" name="m.mkv" duration="7200"/>')
    assert _schedule_overlap_window(movie) == 7200
    assert _schedule_overlap_window(movie, starts_in=600) == 7800
    # A 2h movie overlaps a schedule 90 min out that the old fixed 30-min window missed
    assert _find_nearby_schedule(far, NOW, _schedule_overlap_window(movie)) is not None
    # A 20-min short ending before a schedule 25 min out doesn't trigger
    short = ET.fromstring('<leaf id="2" name="s.mkv" duration="1200"/>')
    assert _find_nearby_schedule(just_inside, NOW, _schedule_overlap_window(short)) is None

    # Unknown runtime (no target, VLC not parsed yet, or -1) -> fixed fallback window
    fallback = float(Config.SCHEDULE_PROXIMITY_WINDOW_SECONDS)
    assert _schedule_overlap_window() == fallback
    assert _schedule_overlap_window(ET.fromstring('<leaf id="3" duration="0"/>')) == fallback
    assert _schedule_overlap_window(ET.fromstring('<leaf id="4" duration="-1"/>')) == fallback

    # Unknown in VLC's playlist but the file is readable -> probed runtime is used
    import shutil, subprocess, tempfile, pathlib
    if shutil.which("ffmpeg"):
        with tempfile.TemporaryDirectory() as tmp:
            clip = pathlib.Path(tmp) / "clip test.mp4"
            subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
                            "testsrc=duration=95:size=160x120:rate=5", str(clip)], check=True)
            unparsed = ET.Element("leaf", id="5", duration="-1", uri=clip.as_uri())
            assert _schedule_overlap_window(unparsed, starts_in=60) == 155
    else:
        print("skip: ffmpeg not found, probe case not run")

    print(f"OK  ({os.path.basename(__file__)}): all schedule-proximity guard cases pass")


if __name__ == "__main__":
    main()
