"""Ties detection, safety alerts, and pump control together. No network I/O of its own."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from .config import Config
from .debris import ActionKind, DebrisReading, PumpAction, PumpPolicy, score_debris
from .notify import Notifier
from .pump import PumpController
from .roboflow_client import Detector
from .safety import Alert, AlertThrottle, evaluate, summarize

log = logging.getLogger(__name__)


@dataclass
class BatchResult:
    frames: int
    alert: Alert | None
    alert_sent: bool
    reading: DebrisReading
    swimmers: int
    action: PumpAction


class DecisionLog:
    """Append-only JSONL log: the training data for the later scheduling model."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, record: dict) -> None:
        with self.path.open("a") as fh:
            fh.write(json.dumps(record, default=str) + "\n")


class Pipeline:
    def __init__(
        self,
        config: Config,
        detector: Detector,
        pump: PumpController,
        notifier: Notifier,
        decision_log: DecisionLog | None = None,
    ) -> None:
        self.config = config
        self.detector = detector
        self.pump = pump
        self.notifier = notifier
        self.log = decision_log
        self.policy = PumpPolicy(config.debris, config.pump)
        self.throttle = AlertThrottle(config.safety.alert_cooldown_seconds)
        self._last_reading = DebrisReading(0, 0.0)
        self._swimmers = 0

    async def process(self, frames: list[Path], now: datetime) -> BatchResult:
        """Handle one batch of frames (one snapshot, or the frames from one motion clip)."""
        cfg = self.config
        worst: tuple[Alert, Path] | None = None
        reading = DebrisReading(0, 0.0)
        swimmers = 0

        for frame in frames:
            try:
                dets = self.detector.detect(frame)
            except Exception:
                log.exception("detection failed for %s", frame)
                continue
            alert = evaluate(dets, now, cfg.safety, cfg.water_zone, cfg.deck_zone, cfg.gate_zone)
            if alert and (worst is None or alert.severity > worst[0].severity):
                worst = (alert, frame)
            scene = summarize(dets, cfg.safety, cfg.water_zone, cfg.deck_zone, cfg.gate_zone)
            swimmers = max(swimmers, len(scene.in_water))
            r = score_debris(dets, cfg.water_zone, cfg.debris)
            if (r.count, r.coverage_pct) > (reading.count, reading.coverage_pct):
                reading = r

        sent = False
        if worst is not None and self.throttle.should_send(worst[0], now):
            self.notifier.send(worst[0], worst[1])
            sent = True

        fresh = bool(frames)
        if fresh:
            self._last_reading, self._swimmers = reading, swimmers
        action = self.policy.tick(self._last_reading, now, self._swimmers > 0, fresh=fresh)
        await self._apply(action)

        result = BatchResult(len(frames), worst[0] if worst else None, sent,
                             self._last_reading, self._swimmers, action)
        if self.log is not None:
            self.log.write({
                "ts": now.isoformat(),
                "frames": [str(f) for f in frames],
                "debris_count": result.reading.count,
                "debris_coverage_pct": result.reading.coverage_pct,
                "swimmers": result.swimmers,
                "alert": asdict(result.alert) if result.alert else None,
                "alert_sent": sent,
                "action": action.kind.value,
                "action_reason": action.reason,
                "pump_state": self.policy.state.value,
                "boost_minutes_today": round(self.policy.minutes_used_today, 1),
            })
        return result

    async def tick(self, now: datetime) -> BatchResult:
        """Advance timers with no new frames (ends boosts on time)."""
        return await self.process([], now)

    async def _apply(self, action: PumpAction) -> None:
        if action.kind is ActionKind.NONE:
            return
        log.info("pump %s: %s", action.kind.value, action.reason)
        try:
            if action.kind in (ActionKind.BOOST, ActionKind.EXTEND):
                await self.pump.set_speed(action.speed_pct)
            elif action.kind is ActionKind.RESTORE:
                await self.pump.restore_schedule()
        except Exception:
            log.exception("pump command failed")
