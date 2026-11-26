"""Runnable self-check for resolving a schedule after playlist cleanup / upgrades.

Run from the repo root:  python tests/test_schedule_rebind.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import xml.etree.ElementTree as ET  # noqa: E402

from src.cogs.scheduler import _resolve_schedule_index  # noqa: E402


def _leaf(name, folder="/movies"):
    return ET.Element("leaf", {"name": name, "uri": f"file://{folder}/{name.replace(' ', '%20')}", "id": name})


def main():
    old = _leaf("Heat (1995) 720p.mkv")
    other = _leaf("Alien (1979).mkv")
    entry = {"number": 2, "title": "Heat", "uri": old.get("uri")}

    # Same file, index shifted by cleanup -> follows the uri
    assert _resolve_schedule_index(entry, [old, other]) == 0

    # File replaced by an upgrade appended at the end -> matches title + year
    upgrade = _leaf("Heat.1995.2160p.UHD.BluRay.x265.mkv")
    remake = _leaf("Heat (2030).mkv")
    assert _resolve_schedule_index(entry, [other, remake, upgrade]) == 2

    # No upgrade yet -> nothing, never a different movie at the old index
    assert _resolve_schedule_index(entry, [other, remake]) is None

    # Old file still in playlist (no cleanup yet) but deleted on disk -> upgrade wins
    gone = {old.get("uri")}
    exists = lambda uri: uri not in gone
    assert _resolve_schedule_index(entry, [old, other, upgrade], exists) == 2

    # Old file missing on disk and no upgrade -> keep the exact entry, let VLC try
    assert _resolve_schedule_index(entry, [other, old], exists) == 1

    # Upgrade that is itself missing on disk is not picked
    gone.add(upgrade.get("uri"))
    assert _resolve_schedule_index(entry, [old, upgrade], exists) == 0

    # Legacy entry without uri still uses the number
    assert _resolve_schedule_index({"number": 2, "title": "Alien"}, [old, other]) == 1
    assert _resolve_schedule_index({"number": 5, "title": "Alien"}, [old, other]) is None

    print("ok")


if __name__ == "__main__":
    main()
