"""Stuck debris: survives a full boost cycle -> ignored until its spot is clear."""

from pathlib import Path

import pytest

from poolwatch.config import config_from_dict
from poolwatch.debris import ActionKind
from poolwatch.history import EventStore
from poolwatch.notify import RecordingNotifier
from poolwatch.pipeline import Pipeline
from poolwatch.pump import FakePump
from poolwatch.stuck import StuckDebris

from conftest import at, leaf

STUCK = [leaf(150, 150)]
SAME_SPOT_DRIFTED = [leaf(152, 149)]
ELSEWHERE = [leaf(120, 180)]


class ScriptedDetector:
    def __init__(self, script):
        self.script = script

    def detect(self, image_path):
        value = self.script[Path(image_path).name]
        if isinstance(value, Exception):
            raise value
        return value


@pytest.fixture
def cfg(raw):
    raw.pop("debris")  # shipped defaults: 1 item, 2 in a row, stuck after 1 cycle
    raw["pump"] = {"no_boost_hours": ["22:00-07:00"]}  # 90% x 15 min, 2 extensions
    return config_from_dict(raw)


SCRIPT = {"stuck.jpg": STUCK, "drift.jpg": SAME_SPOT_DRIFTED, "else.jpg": ELSEWHERE,
          "clean.jpg": [], "both.jpg": STUCK + ELSEWHERE, "fail.jpg": RuntimeError("api")}


def make(cfg, tmp_path):
    pump = FakePump()
    store = EventStore(tmp_path)
    pipe = Pipeline(cfg, ScriptedDetector(SCRIPT), pump, RecordingNotifier(), events=store)
    return pipe, pump, store


async def run_full_cycle(pipe):
    """Debris that never clears: boost, extend twice, give up at 12:48."""
    for t in [(12, 0), (12, 3), (12, 18), (12, 33)]:
        await pipe.process([Path("stuck.jpg")], at(*t))
    return await pipe.process([Path("stuck.jpg")], at(12, 48))


async def test_debris_surviving_a_full_cycle_is_ignored(cfg, tmp_path):
    pipe, pump, store = make(cfg, tmp_path)
    end = await run_full_cycle(pipe)
    assert end.action.kind is ActionKind.RESTORE
    assert end.summary.headline == "Debris won't clear: ignoring 1 stuck item"
    assert "up to 45 min at 90%" in end.summary.detail
    assert len(pipe.stuck.spots) == 1
    assert pump.calls == [("set_speed", 90)] * 3 + [("restore_schedule", None)]
    assert store.state()["stuck"][0]["x"] == 150


async def test_stuck_spot_no_longer_triggers_even_after_cooldown(cfg, tmp_path):
    pipe, pump, store = make(cfg, tmp_path)
    await run_full_cycle(pipe)
    calls = len(pump.calls)
    for minute in range(51, 60 + 60, 3):  # an hour of the same stuck leaf, drifting a bit
        r = await pipe.process([Path("drift.jpg" if minute % 2 else "stuck.jpg")],
                               at(12 + minute // 60, minute % 60))
        assert r.reading.count == 0
    assert len(pump.calls) == calls
    assert "Ignoring 1 stuck item" in r.summary.detail
    frame = store.recent(1)[0]["frames"][0]["detections"][0]
    assert frame["ignored"] is True


async def test_new_debris_elsewhere_still_boosts(cfg, tmp_path):
    pipe, pump, _ = make(cfg, tmp_path)
    await run_full_cycle(pipe)                     # cooldown until 13:03
    await pipe.process([Path("both.jpg")], at(13, 3))
    r = await pipe.process([Path("both.jpg")], at(13, 6))
    assert r.action.kind is ActionKind.BOOST and r.reading.count == 1  # only the new one


async def test_spot_released_after_two_clear_images(cfg, tmp_path):
    pipe, pump, _ = make(cfg, tmp_path)
    await run_full_cycle(pipe)
    await pipe.process([Path("clean.jpg")], at(12, 51))
    assert pipe.stuck.spots
    r = await pipe.process([Path("clean.jpg")], at(12, 54))
    assert pipe.stuck.spots == []
    assert r.summary.headline.startswith("Stuck debris gone: watching that spot again")
    # Debris back at that spot counts again.
    r = await pipe.process([Path("stuck.jpg")], at(13, 3))
    assert r.reading.count == 1


async def test_a_seen_image_resets_the_clear_count(cfg, tmp_path):
    pipe, _, _ = make(cfg, tmp_path)
    await run_full_cycle(pipe)
    await pipe.process([Path("clean.jpg")], at(12, 51))
    await pipe.process([Path("stuck.jpg")], at(12, 54))   # still there after all
    await pipe.process([Path("clean.jpg")], at(12, 57))
    assert len(pipe.stuck.spots) == 1


async def test_failed_detection_does_not_count_as_clear(cfg, tmp_path):
    pipe, _, _ = make(cfg, tmp_path)
    await run_full_cycle(pipe)
    await pipe.process([Path("fail.jpg")], at(12, 51))
    await pipe.process([Path("fail.jpg")], at(12, 54))
    assert len(pipe.stuck.spots) == 1


async def test_debris_cleared_by_the_boost_is_not_marked(cfg, tmp_path):
    pipe, _, _ = make(cfg, tmp_path)
    await pipe.process([Path("stuck.jpg")], at(12))
    await pipe.process([Path("stuck.jpg")], at(12, 3))
    r = await pipe.process([Path("clean.jpg")], at(12, 18))
    assert r.action.kind is ActionKind.RESTORE and pipe.stuck.spots == []


async def test_can_be_turned_off(raw, tmp_path):
    raw.pop("debris")
    raw["debris"] = {"ignore_stuck": False}
    raw["pump"] = {"no_boost_hours": ["22:00-07:00"]}
    pipe, _, _ = make(config_from_dict(raw), tmp_path)
    end = await run_full_cycle(pipe)
    assert end.action.kind is ActionKind.RESTORE and pipe.stuck.spots == []
    assert end.summary.headline == "Retest: debris remains, max extensions reached"


async def test_needs_several_dirty_cycles_when_configured(raw, tmp_path):
    raw.pop("debris")
    raw["debris"] = {"stuck_after_cycles": 2}
    raw["pump"] = {"no_boost_hours": ["22:00-07:00"], "cooldown_minutes": 15}
    pipe, _, _ = make(config_from_dict(raw), tmp_path)
    await run_full_cycle(pipe)
    assert pipe.stuck.spots == []                  # first dirty cycle: keep trying
    for t in [(13, 3), (13, 6), (13, 21), (13, 36)]:  # second cycle boosts at 13:06
        await pipe.process([Path("stuck.jpg")], at(*t))
    end = await pipe.process([Path("stuck.jpg")], at(13, 51))
    assert end.summary.headline.startswith("Debris won't clear") and len(pipe.stuck.spots) == 1
    assert "2 full boost cycles" in end.summary.detail


def test_match_radius_scales_with_the_water_zone(cfg):
    t = StuckDebris(cfg.debris, cfg.water_zone)    # zone is 100 px wide -> 3 px radius
    t.mark(STUCK, at(12))
    assert t.is_ignored(leaf(155, 150))            # within box half-size (2) + 3
    assert not t.is_ignored(leaf(160, 150))
