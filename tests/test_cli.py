import asyncio
import logging
from types import SimpleNamespace

import pytest

from poolwatch import cli
from poolwatch.ring_source import RingSource


def test_ctrl_c_exits_cleanly(monkeypatch, caplog):
    def boom(args):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "_dispatch", boom)
    with caplog.at_level(logging.INFO, logger="poolwatch"):
        cli.main(["collect", "--hours", "1"])  # must not raise
    assert "stopped" in caplog.text


class FlakyRing:
    """Fails once, then works."""

    def __init__(self):
        self.calls = 0

    async def snapshot(self):
        return None

    async def new_motion_frames(self):
        self.calls += 1
        if self.calls == 1:
            raise ConnectionError("ring api hiccup")
        return []


async def test_collect_loop_survives_errors(monkeypatch, caplog):
    cycles = {"n": 0}

    async def fast_sleep(_):
        cycles["n"] += 1
        if cycles["n"] >= 3:
            raise asyncio.CancelledError  # stop the loop after 3 cycles

    monkeypatch.setattr(cli.asyncio, "sleep", fast_sleep)
    cfg = SimpleNamespace(ring=SimpleNamespace(snapshot_interval_minutes=3))
    ring = FlakyRing()
    with caplog.at_level(logging.ERROR, logger="poolwatch"):
        with pytest.raises(asyncio.CancelledError):
            await cli._collect_loop(cfg, ring, hours=1)
    assert ring.calls == 3  # kept going after the first failure
    assert "collection error" in caplog.text


async def test_ring_source_close_closes_session(tmp_path):
    closed = []

    class FakeAuth:
        async def async_close(self):
            closed.append(True)

    src = RingSource("Backyard", str(tmp_path / "t.json"), str(tmp_path))
    src._auth = FakeAuth()
    await src.close()
    await src.close()  # second close is a no-op
    assert closed == [True]
