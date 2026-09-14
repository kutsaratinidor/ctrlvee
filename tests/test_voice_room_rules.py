"""Runnable self-check for the play-start guard's pure decision rule.

Run from the repo root:  python tests/test_voice_room_rules.py
Fails loudly if the voice-room presence/hijack logic regresses.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.cogs.playback import _decide_playback_allow  # noqa: E402


def _check(desc, *args, expected_allowed, expected_reason=""):
    allowed, reason = _decide_playback_allow(*args)
    assert allowed is expected_allowed, (
        f"[{desc}] expected allowed={expected_allowed}, got {allowed} (args={args})"
    )
    assert reason == expected_reason, (
        f"[{desc}] expected reason={expected_reason!r}, got {reason!r}"
    )


# room_resolved, requester_in_room, requester_id, owner_id, owner_holds_current, owner_in_room
def main():
    _check("no room configured -> allow", False, True, 1, None, False, False,
           expected_allowed=True)
    _check("requester not in room -> block", True, False, 2, None, False, False,
           expected_allowed=False, expected_reason="not_in_room")
    _check("requester not in room, owner present -> still block", True, False, 2, 1, True, True,
           expected_allowed=False, expected_reason="not_in_room")
    _check("no owner -> allow", True, True, 2, None, False, False,
           expected_allowed=True)
    _check("same requester owns current -> allow", True, True, 1, 1, True, True,
           expected_allowed=True)
    _check("other user, owner holds current, owner in room -> hijack block", True, True, 2, 1, True, True,
           expected_allowed=False, expected_reason="hijack")
    _check("other user, item changed (owner no longer holds current) -> allow", True, True, 2, 1, False, True,
           expected_allowed=True)
    _check("other user, owner left room -> allow", True, True, 2, 1, True, False,
           expected_allowed=True)
    _check("other user, paused item still held by owner in room -> hijack block", True, True, 2, 1, True, True,
           expected_allowed=False, expected_reason="hijack")
    print(f"OK  ({os.path.basename(__file__)}): all {9} guard cases pass")


if __name__ == "__main__":
    main()