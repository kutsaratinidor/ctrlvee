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
assert svc.sync_folders(["/media/movies"]) == [os.path.normpath("/media/tv")]
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

print("ok")
