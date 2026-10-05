import json
from pathlib import Path

from poolwatch.debris import ActionKind
from poolwatch.notify import RecordingNotifier
from poolwatch.pipeline import DecisionLog, Pipeline
from poolwatch.pump import FakePump
from poolwatch.safety import Severity

from conftest import at, leaf, person


class ScriptedDetector:
    """Returns canned detections per frame name."""

    def __init__(self, script):
        self.script = script

    def detect(self, image_path):
        value = self.script[Path(image_path).name]
        if isinstance(value, Exception):
            raise value
        return value


DIRTY = [leaf(120, 120), leaf(140, 140), leaf(160, 160)]


def make(cfg, script, tmp_path):
    pump, notes = FakePump(), RecordingNotifier()
    log = DecisionLog(tmp_path / "decisions.jsonl")
    return Pipeline(cfg, ScriptedDetector(script), pump, notes, log), pump, notes, log


async def test_unsupervised_swimmer_alerts_with_frame(cfg, tmp_path):
    pipe, pump, notes, _ = make(cfg, {"a.jpg": [person(150, 150)]}, tmp_path)
    result = await pipe.process([Path("a.jpg")], at(14))
    assert result.alert_sent
    alert, image = notes.sent[0]
    assert alert.severity is Severity.CRITICAL and image == Path("a.jpg")


async def test_worst_alert_across_clip_frames_is_sent_once(cfg, tmp_path):
    script = {"1.jpg": [], "2.jpg": [person(450, 50)], "3.jpg": [person(150, 150)]}
    pipe, _, notes, _ = make(cfg, script, tmp_path)
    await pipe.process([Path(n) for n in script], at(23))
    assert len(notes.sent) == 1
    assert notes.sent[0][0].rule == "in_water_quiet_hours"
    assert notes.sent[0][1] == Path("3.jpg")


async def test_repeat_alerts_throttled(cfg, tmp_path):
    pipe, _, notes, _ = make(cfg, {"a.jpg": [person(150, 150)]}, tmp_path)
    await pipe.process([Path("a.jpg")], at(14))
    await pipe.process([Path("a.jpg")], at(14, 1))
    assert len(notes.sent) == 1


async def test_debris_boosts_pump_then_restores(cfg, tmp_path):
    pipe, pump, _, _ = make(cfg, {"d.jpg": DIRTY, "c.jpg": []}, tmp_path)
    await pipe.process([Path("d.jpg")], at(12))
    r = await pipe.process([Path("d.jpg")], at(12, 15))
    assert r.action.kind is ActionKind.BOOST
    assert pump.calls == [("set_speed", 75)]
    await pipe.process([Path("c.jpg")], at(12, 30))
    r = await pipe.tick(at(12, 46))
    assert r.action.kind is ActionKind.RESTORE
    assert pump.calls[-1] == ("restore_schedule", None)


async def test_swimmer_prevents_boost(cfg, tmp_path):
    script = {"x.jpg": DIRTY + [person(150, 150), person(50, 50)]}
    pipe, pump, notes, _ = make(cfg, script, tmp_path)
    await pipe.process([Path("x.jpg")], at(12))
    r = await pipe.process([Path("x.jpg")], at(12, 15))
    assert r.action.kind is ActionKind.NONE and r.swimmers == 1
    assert pump.calls == []
    assert notes.sent == []  # supervised daytime swim


async def test_detector_failure_is_survivable(cfg, tmp_path):
    script = {"bad.jpg": RuntimeError("api down"), "a.jpg": [person(150, 150)]}
    pipe, _, notes, _ = make(cfg, script, tmp_path)
    r = await pipe.process([Path("bad.jpg"), Path("a.jpg")], at(14))
    assert r.alert_sent and len(notes.sent) == 1


async def test_pump_failure_does_not_crash(cfg, tmp_path):
    class BrokenPump(FakePump):
        async def set_speed(self, speed_pct):
            raise OSError("controller offline")

    pipe = Pipeline(cfg, ScriptedDetector({"d.jpg": DIRTY}), BrokenPump(), RecordingNotifier())
    await pipe.process([Path("d.jpg")], at(12))
    r = await pipe.process([Path("d.jpg")], at(12, 15))
    assert r.action.kind is ActionKind.BOOST


async def test_decision_log_records_every_batch(cfg, tmp_path):
    pipe, _, _, log = make(cfg, {"d.jpg": DIRTY}, tmp_path)
    await pipe.process([Path("d.jpg")], at(12))
    await pipe.tick(at(12, 1))
    rows = [json.loads(line) for line in log.path.read_text().splitlines()]
    assert len(rows) == 2
    assert rows[0]["debris_count"] == 3 and rows[0]["frames"] == ["d.jpg"]
    assert rows[1]["frames"] == [] and rows[1]["pump_state"] == "idle"
