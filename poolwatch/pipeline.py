"""Ties detection, safety alerts, and pump control together. No network I/O of its own."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from .config import Config
from .debris import (ActionKind, DebrisReading, PumpAction, PumpPolicy, debris_in_water,
                     reading_for)
from .detections import Detection
from .history import EventStore
from .notify import Notifier
from .pump import PumpController
from .roboflow_client import Detector
from .safety import Alert, AlertThrottle, evaluate, summarize
from .stuck import StuckDebris
from .summary import Summary, describe

log = logging.getLogger(__name__)


@dataclass
class BatchResult:
    frames: int
    alert: Alert | None
    alert_sent: bool
    reading: DebrisReading
    swimmers: int
    action: PumpAction
    summary: Summary | None = None


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
        away_source=None,
        events: EventStore | None = None,
        mode: str = "dry-run",
    ) -> None:
        self.config = config
        self.detector = detector
        self.pump = pump
        self.notifier = notifier
        self.log = decision_log
        self.away_source = away_source  # anything with is_away(datetime) -> bool
        self.policy = PumpPolicy(config.debris, config.pump)
        self.throttle = AlertThrottle(config.safety.alert_cooldown_seconds)
        self._last_reading = DebrisReading(0, 0.0)
        self._swimmers = 0
        self.events = events
        self.mode = mode  # "live", "dry-run" or "simulated"; shown on the dashboard
        self._speed_pct: int | None = None  # None = on its normal schedule
        self.stuck = StuckDebris(config.debris, config.water_zone)
        self._dirty_cycles = 0  # boost cycles in a row that ended with debris still there
        self._last_debris: list[Detection] = []  # counted debris in the latest reading
        self._last_image: str | None = None

    async def process(self, frames: list[Path], now: datetime) -> BatchResult:
        """Handle one batch of frames (one snapshot, or the frames from one motion clip)."""
        cfg = self.config
        worst: tuple[Alert, Path] | None = None
        reading = DebrisReading(0, 0.0)
        swimmers = 0
        away = False
        if self.away_source is not None and frames:
            try:
                away = self.away_source.is_away(now)
            except Exception:
                log.exception("away check failed; treating as home")

        per_frame: list[dict] = []
        counted_debris: list[Detection] = []
        all_debris: list[Detection] = []  # including ignored, to see if stuck spots cleared
        detected = False
        for frame in frames:
            try:
                dets = self.detector.detect(frame)
            except Exception:
                log.exception("detection failed for %s", frame)
                per_frame.append({"path": str(frame), "error": "detection failed", "detections": []})
                continue
            detected = True
            per_frame.append(self._frame_record(frame, dets))
            alert = evaluate(dets, now, cfg.safety, cfg.water_zone, cfg.deck_zone, cfg.gate_zone,
                             away=away)
            if alert and (worst is None or alert.severity > worst[0].severity):
                worst = (alert, frame)
            scene = summarize(dets, cfg.safety, cfg.water_zone, cfg.deck_zone, cfg.gate_zone)
            swimmers = max(swimmers, len(scene.in_water))
            on_water = debris_in_water(dets, cfg.water_zone, cfg.debris)
            all_debris.extend(on_water)
            counted = [d for d in on_water if not self.stuck.is_ignored(d)]
            r = reading_for(counted, cfg.water_zone)
            if (r.count, r.coverage_pct) > (reading.count, reading.coverage_pct):
                reading, counted_debris = r, counted

        sent = False
        if worst is not None and self.throttle.should_send(worst[0], now):
            self.notifier.send(worst[0], worst[1])
            sent = True

        fresh = bool(frames)
        released = []
        if fresh:
            self._last_reading, self._swimmers = reading, swimmers
            self._last_debris = counted_debris
            if detected:  # a failed detection says nothing about whether a spot cleared
                released = self.stuck.observe(all_debris)
        action = self.policy.tick(self._last_reading, now, self._swimmers > 0, fresh=fresh)
        newly_ignored = self._track_stuck(action, now)
        await self._apply(action)

        p = self.policy
        summary = describe(
            action, self._last_reading, streak=p.high_streak, debris=cfg.debris, pump=cfg.pump,
            state=p.state, boost_until=p.boost_until, cooldown_until=p.cooldown_until,
            extensions=p.extensions, ignored=len(self.stuck.spots),
            newly_ignored=newly_ignored, released=len(released),
            dirty_cycles=self._dirty_cycles if not newly_ignored else cfg.debris.stuck_after_cycles,
        )
        if newly_ignored:
            self._dirty_cycles = 0
        result = BatchResult(len(frames), worst[0] if worst else None, sent,
                             self._last_reading, self._swimmers, action, summary)
        if self.events is not None:
            self._record(result, per_frame, now, away)
        if self.log is not None:
            self.log.write({
                "ts": now.isoformat(),
                "frames": [str(f) for f in frames],
                "debris_count": result.reading.count,
                "debris_coverage_pct": result.reading.coverage_pct,
                "swimmers": result.swimmers,
                "away": away,
                "alert": asdict(result.alert) if result.alert else None,
                "alert_sent": sent,
                "action": action.kind.value,
                "action_reason": action.reason,
                "summary": summary.headline,
                "pump_state": self.policy.state.value,
                "boost_minutes_today": round(self.policy.minutes_used_today, 1),
            })
        return result

    def _track_stuck(self, action: PumpAction, now: datetime) -> int:
        """After a boost cycle ends, count it as dirty or clean; ignore debris that keeps
        surviving. Returns how many pieces were newly ignored."""
        if action.kind is not ActionKind.RESTORE or action.reason != "boost finished":
            return 0
        if not self._last_reading.is_high(self.config.debris):
            self._dirty_cycles = 0
            return 0
        self._dirty_cycles += 1
        dc = self.config.debris
        if not dc.ignore_stuck or self._dirty_cycles < dc.stuck_after_cycles:
            return 0
        added = self.stuck.mark(self._last_debris, now)
        if added:
            log.info("ignoring %d stuck debris item(s) until they clear", added)
        return added

    def _frame_record(self, frame: Path, dets: list[Detection]) -> dict:
        water = self.config.water_zone
        labels = {l.lower() for l in self.config.debris.debris_labels}
        return {
            "path": str(frame),
            "detections": [
                {"label": d.label, "confidence": round(d.confidence, 3),
                 "x": round(d.x, 1), "y": round(d.y, 1),
                 "width": round(d.width, 1), "height": round(d.height, 1),
                 "kind": "debris" if d.label in labels else
                         "person" if d.label in self.config.safety.person_labels else "other",
                 "in_water": d.in_zone(water, "center"),
                 "ignored": d.label in labels and self.stuck.is_ignored(d)}
                for d in dets
            ],
        }

    def _pump_status(self) -> dict:
        p = self.policy
        return {
            "state": p.state.value,
            "speed_pct": self._speed_pct,  # None means "on its normal schedule"
            "boost_until": p.boost_until,
            "cooldown_until": p.cooldown_until,
            "extensions": p.extensions,
            "max_extensions": self.config.pump.max_extensions,
            "boost_minutes_today": round(p.minutes_used_today, 1),
            "budget_minutes": self.config.pump.max_boost_minutes_per_day,
        }

    def _record(self, result: BatchResult, per_frame: list[dict], now: datetime, away: bool) -> None:
        """Save an event for every batch with images, and for timer-driven pump changes."""
        if per_frame:
            self._last_image = per_frame[-1]["path"]
        cfg = self.config
        reading = result.reading
        common = {
            "reading": {"count": reading.count, "coverage_pct": reading.coverage_pct,
                        "high": reading.is_high(cfg.debris)},
            "streak": self.policy.high_streak,
            "required": cfg.debris.consecutive_readings,
            "swimmers": result.swimmers,
            "pump": self._pump_status(),
            "stuck": [{"x": s.x, "y": s.y, "width": s.width, "height": s.height,
                       "since": s.since, "clear_streak": s.clear_streak}
                      for s in self.stuck.spots],
        }
        latest = None
        if per_frame or result.action.kind is not ActionKind.NONE:
            latest = self.events.append({
                "ts": now,
                "frames": per_frame,
                "image": per_frame[-1]["path"] if per_frame else self._last_image,
                "from_timer": not per_frame,
                **common,
                "alert": asdict(result.alert) if result.alert else None,
                "away": away,
                "action": {"kind": result.action.kind.value, "reason": result.action.reason,
                           "speed_pct": result.action.speed_pct, "minutes": result.action.minutes},
                "summary": asdict(result.summary),
            })
        prev = self.events.state() or {}
        zone = cfg.water_zone
        self.events.write_state({
            "now": now,
            "mode": self.mode,
            **common,
            "latest_event_id": latest["id"] if latest else prev.get("latest_event_id"),
            # The newest event that has an image, for the home page's picture and labels.
            "latest_image_event_id": (latest["id"] if latest and per_frame
                                      else prev.get("latest_image_event_id")),
            "latest_image": self._last_image,
            "summary": asdict(result.summary),
            "coord_size": cfg.zone_image_size,
            "water_zone": {"points": zone.points, "holes": zone.holes},
            "settings": {
                "boost_speed_pct": cfg.pump.boost_speed_pct,
                "boost_minutes": cfg.pump.boost_minutes,
                "consecutive_readings": cfg.debris.consecutive_readings,
                "count_threshold": cfg.debris.count_threshold,
                "coverage_threshold_pct": cfg.debris.coverage_threshold_pct,
            },
        })

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
                self._speed_pct = action.speed_pct
            elif action.kind is ActionKind.RESTORE:
                await self.pump.restore_schedule()
                self._speed_pct = None
        except Exception:
            log.exception("pump command failed")
