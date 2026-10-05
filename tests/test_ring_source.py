"""RingSource clip handling against a fake camera (no Ring account needed)."""

import logging

import pytest

from poolwatch import ring_source
from poolwatch.ring_source import MAX_CLIP_ATTEMPTS, RingSource


class FakeCamera:
    def __init__(self, event_ids, fail_with=None, fail_times=None):
        self.event_ids = event_ids
        self.fail_with = fail_with
        self.fail_times = fail_times  # None = always fail when fail_with is set
        self.downloads = []

    async def async_history(self, limit=10, kind="motion"):
        return [{"id": i} for i in reversed(self.event_ids)]  # newest first, like Ring

    async def async_recording_download(self, event_id, filename):
        self.downloads.append(event_id)
        if self.fail_with is not None and (self.fail_times is None or self.fail_times > 0):
            if self.fail_times is not None:
                self.fail_times -= 1
            raise self.fail_with
        with open(filename, "wb") as fh:
            fh.write(b"mp4")


@pytest.fixture
def source(tmp_path, monkeypatch):
    monkeypatch.setattr(ring_source, "extract_frames",
                        lambda clip, out, count=3: [out / f"{clip.stem}_01.jpg"])
    src = RingSource("Backyard", str(tmp_path / "tok.json"), str(tmp_path / "data"))
    return src


async def test_downloads_new_clips_once(source):
    source._camera = FakeCamera([1, 2])
    frames = await source.new_motion_frames()
    assert [f.name for f in frames] == ["1_01.jpg", "2_01.jpg"]
    assert await source.new_motion_frames() == []
    assert source._camera.downloads == [1, 2]


async def test_missing_clips_give_up_after_max_attempts(source, caplog):
    err = RuntimeError("HTTP error with status code 404 during query")
    source._camera = FakeCamera([1, 2, 3], fail_with=err)
    with caplog.at_level(logging.INFO, logger="poolwatch.ring_source"):
        for _ in range(MAX_CLIP_ATTEMPTS + 3):
            assert await source.new_motion_frames() == []
    # Each clip tried exactly MAX_CLIP_ATTEMPTS times, then abandoned.
    for event_id in (1, 2, 3):
        assert source._camera.downloads.count(event_id) == MAX_CLIP_ATTEMPTS
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "Ring Protect" in warnings[0].getMessage()
    # Nothing logged at INFO per failed clip anymore (that was the spam).
    assert not [r for r in caplog.records if r.levelno == logging.INFO]


async def test_clip_that_becomes_ready_is_picked_up(source):
    err = RuntimeError("HTTP error with status code 404")
    source._camera = FakeCamera([5], fail_with=err, fail_times=1)
    assert await source.new_motion_frames() == []
    frames = await source.new_motion_frames()
    assert [f.name for f in frames] == ["5_01.jpg"]


async def test_prime_skips_existing_events(source):
    source._camera = FakeCamera([1, 2])
    await source.prime()
    assert await source.new_motion_frames() == []
    source._camera.event_ids.append(3)
    frames = await source.new_motion_frames()
    assert [f.name for f in frames] == ["3_01.jpg"]


class FakeResp:
    def __init__(self, content):
        self.content = content


class FakeRing:
    def __init__(self, stored):
        self.stored = stored
        self.urls = []

    async def async_query(self, url, **kwargs):
        self.urls.append(url)
        if isinstance(self.stored, Exception):
            raise self.stored
        return FakeResp(self.stored)


class SnapCamera:
    id = 4242

    def __init__(self, fresh, stored=b""):
        self.fresh = list(fresh)  # successive return values of async_get_snapshot
        self._ring = FakeRing(stored)
        self.snapshot_kwargs = []

    async def async_get_snapshot(self, **kwargs):
        self.snapshot_kwargs.append(kwargs)
        return self.fresh.pop(0) if self.fresh else None


async def test_snapshot_waits_longer_than_library_default(source):
    source._camera = SnapCamera([b"jpg1"])
    path = await source.snapshot()
    assert path.read_bytes() == b"jpg1"
    kw = source._camera.snapshot_kwargs[0]
    assert kw["retries"] * kw["delay"] >= 15


async def test_falls_back_to_stored_snapshot(source):
    source._camera = SnapCamera([None], stored=b"stored-jpg")
    path = await source.snapshot()
    assert path.read_bytes() == b"stored-jpg"
    assert source._camera._ring.urls == ["/clients_api/snapshots/image/4242"]


async def test_identical_snapshot_skipped(source):
    source._camera = SnapCamera([None, None], stored=b"same")
    assert await source.snapshot() is not None
    assert await source.snapshot() is None


async def test_no_snapshot_anywhere_returns_none(source):
    source._camera = SnapCamera([None], stored=RuntimeError("404"))
    assert await source.snapshot() is None
