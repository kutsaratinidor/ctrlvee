"""Runnable self-check for watch-folder removal (in-memory sync + playlist purge).

Run from the repo root:  python tests/test_watch_remove.py
"""
import os
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import path_is_under
from src.services.vlc_controller import VLCController
from src.services.watch_folder_service import WatchFolderService

assert path_is_under("/media/tv/a.mkv", "/media/tv")
assert path_is_under("/media/tv", "/media/tv/")
assert not path_is_under("/media/tv2/a.mkv", "/media/tv")

# sync_folders drops removed folders and forgets their seen/pending files
svc = WatchFolderService(vlc_controller=None, folders=["/media/tv", "/media/movies"])
svc._seen = {"/media/tv/a.mkv", "/media/movies/b.mkv"}
svc._pending = {"/media/tv/c.mkv": (1, 0), "/media/movies/d.mkv": (1, 0)}
# Background reloads keep seen files, so a transient config glitch can't re-enqueue a folder
svc.sync_folders(["/media/movies"])
assert "/media/tv/a.mkv" in svc._seen
svc.sync_folders(["/media/tv", "/media/movies"])
# Explicit removal forgets them
assert svc.sync_folders(["/media/movies"], forget_removed=True) == [os.path.normpath("/media/tv")]
assert svc.folders == [os.path.normpath("/media/movies")]
assert svc._seen == {"/media/movies/b.mkv"}
assert list(svc._pending) == ["/media/movies/d.mkv"]

# remove_playlist_items_under deletes matching items, leaves the current one and others
vlc = VLCController.__new__(VLCController)
vlc.logger = __import__("logging").getLogger("test")
vlc.get_playlist = lambda: ET.fromstring(
    '<node>'
    '<leaf id="1" name="a" uri="file:///media/tv/a.mkv"/>'
    '<leaf id="2" name="b" uri="file:///media/tv/b.mkv" current="current"/>'
    '<leaf id="3" name="c" uri="file:///media/tv2/c.mkv"/>'
    '</node>'
)
deleted = []
vlc.delete_playlist_item = lambda i: deleted.append(i) or True
res = vlc.remove_playlist_items_under("/media/tv")
assert deleted == ["1"], deleted
assert res["removed"] == 1 and res["skipped_current"] is True

# Race: a scan that read the folder list before a removal must not re-add the folder.
# Replays it: the scan reads [A, B] and pauses, the removal runs, then the scan resumes.
import tempfile
import threading
import time

import dotenv
from src.cogs import watch
from src.services import watch_folder_service as wfs

dotenv.load_dotenv = lambda *a, **k: None  # keep the scan off the real .env
a_dir, b_dir = tempfile.mkdtemp(), tempfile.mkdtemp()
config = [a_dir, b_dir]
watch.get_watch_folders_from_env = lambda: list(config)
watch._write_env_watch_folders = lambda new: (config.__setitem__(slice(None), new), True)[1]

started, proceed = threading.Event(), threading.Event()
def stale_read():
    snapshot = list(config)
    started.set()
    proceed.wait(5)
    return snapshot
wfs.get_watch_folders_from_env = stale_read

svc = WatchFolderService(vlc_controller=None, folders=[a_dir, b_dir])
svc._first_scan_done = True
scan = threading.Thread(target=svc._scan_all, args=(False,))
scan.start()
started.wait(5)
remove = threading.Thread(target=watch.remove_watch_folder, args=(a_dir, svc, None, False))
remove.start()
time.sleep(0.3)  # without the lock, the removal finishes here, before the scan resumes
proceed.set()
scan.join(5)
remove.join(5)
assert svc.folders == [os.path.normpath(b_dir)], svc.folders
assert config == [b_dir], config

print("ok")
