"""Runnable self-check for the owner-only upgrade command's git pull.

Run from the repo root:  python tests/test_self_update.py
"""
import asyncio
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils.self_update import pull_update  # noqa: E402


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True,
                   capture_output=True)


def commit(cwd, name, text, msg):
    with open(os.path.join(cwd, name), "w") as f:
        f.write(text)
    git(cwd, "add", name)
    git(cwd, "commit", "-m", msg)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        origin, clone = os.path.join(tmp, "origin"), os.path.join(tmp, "clone")
        os.mkdir(origin)
        git(origin, "init", "-q")
        commit(origin, "a.txt", "1", "first")
        git(tmp, "clone", "-q", origin, clone)

        restart, msg = asyncio.run(pull_update(clone))
        assert not restart and msg == "Already up to date.", msg

        commit(origin, "a.txt", "2", "second change")
        restart, msg = asyncio.run(pull_update(clone))
        assert restart and "second change" in msg, msg

        # A local edit that conflicts blocks the pull: report it, keep running
        commit(origin, "a.txt", "3", "third")
        with open(os.path.join(clone, "a.txt"), "w") as f:
            f.write("local edit")
        restart, msg = asyncio.run(pull_update(clone))
        assert not restart and msg.startswith("git pull failed"), msg
    print("self_update: ok")


if __name__ == "__main__":
    main()
