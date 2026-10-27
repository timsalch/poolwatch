"""The demo scenario end to end: debris in 2 images -> 90% for 15 min -> retest."""

import json
from datetime import timedelta
from pathlib import Path

import pytest

from poolwatch.config import config_from_dict
from poolwatch.debris import ActionKind
from poolwatch.history import EventStore
from poolwatch.notify import RecordingNotifier
from poolwatch.pipeline import Pipeline
from poolwatch.pump import FakePump
from poolwatch.watch import replay

from conftest import at, leaf, person


class ScriptedDetector:
    def __init__(self, script):
        self.script = script

    def detect(self, image_path):
        return self.script[Path(image_path).name]


@pytest.fixture
def demo_cfg(raw):
    # The shipped defaults: any debris counts, 2 in a row, 90% for 15 min.
    raw.pop("debris")
    raw["pump"] = {"no_boost_hours": ["22:00-07:00"]}
    return config_from_dict(raw)


def make(cfg, script, tmp_path):
    pump = FakePump()
    store = EventStore(tmp_path)
    return Pipeline(cfg, ScriptedDetector(script), pump, RecordingNotifier(),
                    events=store, mode="simulated"), pump, store


def test_defaults_match_the_spec(demo_cfg):
    assert demo_cfg.pump.boost_speed_pct == 90
    assert demo_cfg.pump.boost_minutes == 15
    assert demo_cfg.debris.consecutive_readings == 2
    assert demo_cfg.debris.count_threshold == 1


async def test_two_dirty_images_boost_to_90_for_15_minutes(demo_cfg, tmp_path):
    pipe, pump, _ = make(demo_cfg, {"d.jpg": [leaf(150, 150)], "c.jpg": []}, tmp_path)
    first = await pipe.process([Path("d.jpg")], at(12))
    assert first.action.kind is ActionKind.NONE
    assert first.summary.headline == "Debris spotted (1 of 2), waiting to confirm"
    second = await pipe.process([Path("d.jpg")], at(12, 3))
    assert second.action.kind is ActionKind.BOOST
    assert (second.action.speed_pct, second.action.minutes) == (90, 15)
    assert pump.calls == [("set_speed", 90)]
    assert second.summary.headline == "Pump boosted to 90% for 15 min"
    assert "Retest at 12:18 PM" in second.summary.detail


async def test_one_dirty_image_between_clean_ones_never_boosts(demo_cfg, tmp_path):
    pipe, pump, _ = make(demo_cfg, {"d.jpg": [leaf(150, 150)], "c.jpg": []}, tmp_path)
    for i, name in enumerate(["d.jpg", "c.jpg", "d.jpg", "c.jpg"]):
        await pipe.process([Path(name)], at(12, 3 * i))
    assert pump.calls == []


async def test_retest_dirty_extends_then_clean_restores(demo_cfg, tmp_path):
    pipe, pump, _ = make(demo_cfg, {"d.jpg": [leaf(150, 150)], "c.jpg": []}, tmp_path)
    await pipe.process([Path("d.jpg")], at(12))
    await pipe.process([Path("d.jpg")], at(12, 3))            # boost until 12:18
    mid = await pipe.process([Path("d.jpg")], at(12, 9))
    assert mid.action.kind is ActionKind.NONE and mid.summary.tone == "active"
    retest = await pipe.process([Path("d.jpg")], at(12, 18))  # still dirty
    assert retest.action.kind is ActionKind.EXTEND
    assert retest.summary.headline.startswith("Retest: debris still there")
    done = await pipe.process([Path("c.jpg")], at(12, 33))    # clean at the next retest
    assert done.action.kind is ActionKind.RESTORE
    assert done.summary.headline == "Retest: surface clear, pump back on schedule"
    assert pump.calls == [("set_speed", 90), ("set_speed", 90), ("restore_schedule", None)]


async def test_debris_outside_the_water_is_ignored(demo_cfg, tmp_path):
    pipe, pump, _ = make(demo_cfg, {"deck.jpg": [leaf(50, 50)]}, tmp_path)
    await pipe.process([Path("deck.jpg")], at(12))
    r = await pipe.process([Path("deck.jpg")], at(12, 3))
    assert r.action.kind is ActionKind.NONE and pump.calls == []


async def test_events_record_image_labels_and_action(demo_cfg, tmp_path):
    script = {"d.jpg": [leaf(150, 150), leaf(50, 50), person(250, 250)]}
    pipe, _, store = make(demo_cfg, script, tmp_path)
    await pipe.process([Path("d.jpg")], at(12))
    await pipe.process([Path("d.jpg")], at(12, 3))
    ev = store.recent(1)[0]
    assert ev["image"] == "d.jpg" and ev["action"]["kind"] == "boost"
    dets = ev["frames"][0]["detections"]
    assert [(d["kind"], d["in_water"]) for d in dets] == [
        ("debris", True), ("debris", False), ("person", False)]
    assert ev["summary"]["headline"] == "Pump boosted to 90% for 15 min"
    state = store.state()
    assert state["pump"]["state"] == "boosting" and state["pump"]["speed_pct"] == 90
    assert state["latest_event_id"] == ev["id"] and state["mode"] == "simulated"
    # Dates are ISO 8601 so the browser can parse them.
    raw = json.loads(store.state_path.read_text())
    assert raw["now"] == "2026-10-04T12:03:00-07:00"


async def test_timer_retest_without_a_new_image_is_recorded(demo_cfg, tmp_path):
    pipe, _, store = make(demo_cfg, {"d.jpg": [leaf(150, 150)], "c.jpg": []}, tmp_path)
    await pipe.process([Path("d.jpg")], at(12))
    await pipe.process([Path("d.jpg")], at(12, 3))
    await pipe.process([Path("c.jpg")], at(12, 6))
    await pipe.tick(at(12, 10))     # quiet tick: no event
    n = len(store.recent(100))
    r = await pipe.tick(at(12, 18))  # boost ends on the timer
    assert r.action.kind is ActionKind.RESTORE
    ev = store.recent(1)[0]
    assert len(store.recent(100)) == n + 1
    assert ev["from_timer"] and ev["frames"] == [] and ev["image"] == "c.jpg"
    state = store.state()
    assert state["latest_event_id"] == ev["id"]
    assert store.get(state["latest_image_event_id"])["frames"][0]["path"] == "c.jpg"


async def test_replay_plays_the_whole_story(demo_cfg, tmp_path):
    names = ["c1", "d1", "d2", "d3", "d4", "d5", "d6", "c2", "c3"]
    script = {f"{n}.jpg": ([leaf(150, 150)] if n.startswith("d") else []) for n in names}
    pipe, pump, store = make(demo_cfg, script, tmp_path)
    slept = []

    async def no_sleep(s):
        slept.append(s)

    await replay(pipe, [Path(f"{n}.jpg") for n in names], at(10), step_minutes=3,
                 delay_seconds=2, sleep=no_sleep)
    kinds = [e["action"]["kind"] for e in reversed(store.recent(100))]
    # d2 confirms -> boost at 10:06; retest at 10:21 (c2 is clean) -> restore.
    assert kinds == ["none", "none", "boost", "none", "none", "none", "none", "restore", "none"]
    assert pump.calls == [("set_speed", 90), ("restore_schedule", None)]
    assert slept[0] == 2
