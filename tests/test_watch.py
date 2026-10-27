import os
import time

from poolwatch.detections import Detection
from poolwatch.roboflow_client import CachingDetector
from poolwatch.watch import FolderWatcher, list_images


def touch(path, age=10.0):
    path.write_bytes(b"x")
    t = time.time() - age
    os.utime(path, (t, t))
    return path


def test_only_new_settled_images_are_reported(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(); b.mkdir()
    touch(a / "old.jpg")
    w = FolderWatcher([a, b])
    assert w.poll() == []
    new1 = touch(b / "new1.jpg", age=20)
    new2 = touch(a / "new2.png", age=15)
    touch(a / "notes.txt")
    fresh = touch(a / "writing.jpg", age=0)  # still being written
    assert w.poll() == [new1, new2]          # oldest first, across folders
    assert w.poll() == []
    os.utime(fresh, (time.time() - 5, time.time() - 5))
    assert w.poll() == [fresh]


def test_include_existing_and_sorting(tmp_path):
    second = touch(tmp_path / "a.jpg", age=5)
    first = touch(tmp_path / "b.jpg", age=50)
    assert list_images([tmp_path]) == [first, second]
    assert FolderWatcher([tmp_path], include_existing=True).poll() == [first, second]


class CountingDetector:
    def __init__(self):
        self.calls = 0

    def detect(self, image_path):
        self.calls += 1
        return [Detection("leaf", 0.9, 1, 2, 3, 4)]


def test_cache_reuses_results_until_the_image_or_workflow_changes(tmp_path):
    img = touch(tmp_path / "f.jpg")
    inner = CountingDetector()
    cache = CachingDetector(inner, tmp_path / "cache", key="ws/wf-v1")
    assert cache.detect(img) == cache.detect(img) == [Detection("leaf", 0.9, 1, 2, 3, 4)]
    assert inner.calls == 1
    CachingDetector(inner, tmp_path / "cache", key="ws/wf-v2").detect(img)
    assert inner.calls == 2
    img.write_bytes(b"changed")
    cache.detect(img)
    assert inner.calls == 3
