"""Runnable self-check for VLCController.seek_to parsing and bounds.

Run from the repo root:  python tests/test_seek_to.py
"""
import os
import sys
import xml.etree.ElementTree as ET
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.services.vlc_controller import VLCController  # noqa: E402


def make(length):
    vlc = VLCController.__new__(VLCController)
    vlc.get_status = mock.Mock(return_value=ET.fromstring(f"<root><length>{length}</length></root>"))
    vlc.seek = mock.Mock(return_value=ET.fromstring("<root><state>playing</state></root>"))
    return vlc


def main():
    two_hours = 7200
    for ts, expected in (("1:23:45", 5025), ("83:10", 4990), ("90", 90), (" 0:00 ", 0)):
        vlc = make(two_hours)
        ok, msg = vlc.seek_to(ts)
        assert ok, (ts, msg)
        vlc.seek.assert_called_once_with(expected)

    # Out of bounds: at or past the end
    for ts in ("2:00:00", "2:00:01", "7200"):
        vlc = make(two_hours)
        ok, msg = vlc.seek_to(ts)
        assert not ok and "past the end" in msg, (ts, msg)
        vlc.seek.assert_not_called()

    # Malformed
    for ts in ("", "abc", "1:2:3:4", "-5", "1:-5", "1.5", "1::2"):
        vlc = make(two_hours)
        ok, msg = vlc.seek_to(ts)
        assert not ok and "Invalid" in msg, (ts, msg)
        vlc.seek.assert_not_called()

    # Nothing playing / unknown length (streams report -1)
    for length in ("0", "-1", ""):
        vlc = make(length)
        ok, msg = vlc.seek_to("10")
        assert not ok and "known length" in msg, (length, msg)
    vlc = make(two_hours)
    vlc.get_status.return_value = None
    assert not vlc.seek_to("10")[0]

    # VLC request failure
    vlc = make(two_hours)
    vlc.seek.return_value = None
    assert vlc.seek_to("10") == (False, "Could not seek.")
    print("ok")


if __name__ == "__main__":
    main()
