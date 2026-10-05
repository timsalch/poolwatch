"""Surface-debris scoring and the pump boost policy."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum

from .config import DebrisConfig, PumpPolicyConfig
from .detections import Detection, filter_labels
from .geometry import Zone


@dataclass(frozen=True)
class DebrisReading:
    count: int
    coverage_pct: float  # summed debris box area as % of the water zone area

    def is_high(self, config: DebrisConfig) -> bool:
        return (
            self.count >= config.count_threshold
            or self.coverage_pct >= config.coverage_threshold_pct
        )


def score_debris(detections: list[Detection], water: Zone, config: DebrisConfig) -> DebrisReading:
    debris = [
        d for d in filter_labels(detections, config.debris_labels, config.min_confidence)
        if d.in_zone(water, "center")
    ]
    water_area = water.area()
    covered = sum(d.area for d in debris)
    pct = (covered / water_area * 100.0) if water_area else 0.0
    return DebrisReading(count=len(debris), coverage_pct=round(min(pct, 100.0), 3))


class PumpState(Enum):
    IDLE = "idle"
    BOOSTING = "boosting"
    COOLDOWN = "cooldown"


class ActionKind(Enum):
    NONE = "none"
    BOOST = "boost"
    EXTEND = "extend"
    RESTORE = "restore"


@dataclass(frozen=True)
class PumpAction:
    kind: ActionKind
    reason: str
    speed_pct: int | None = None
    minutes: int | None = None


@dataclass
class PumpPolicy:
    """Decides when to boost the filter pump, and when to hand control back to the schedule.

    Pure logic with no I/O: feed it readings, it returns actions. Safety rules,
    in priority order:
      1. Never boost while someone is in the water; end any active boost.
      2. Never boost during no-boost hours; end any active boost.
      3. Respect a daily boost-minute budget and a cooldown between boosts.
    """

    debris: DebrisConfig
    pump: PumpPolicyConfig
    state: PumpState = PumpState.IDLE
    boost_until: datetime | None = None
    cooldown_until: datetime | None = None
    extensions: int = 0
    _high_streak: int = 0
    _budget_day: date | None = None
    _minutes_used: float = 0.0
    _last_tick: datetime | None = field(default=None, repr=False)

    # --- budget bookkeeping -------------------------------------------------
    def _roll_day(self, now: datetime) -> None:
        if self._budget_day != now.date():
            self._budget_day = now.date()
            self._minutes_used = 0.0

    def _account(self, now: datetime) -> None:
        """Charge boost minutes since the last tick against today's budget."""
        if self.state is PumpState.BOOSTING and self._last_tick is not None:
            start = max(self._last_tick, datetime.combine(now.date(), datetime.min.time(), now.tzinfo))
            if now > start:
                self._minutes_used += (now - start).total_seconds() / 60.0
        self._last_tick = now

    @property
    def minutes_used_today(self) -> float:
        return self._minutes_used

    def _budget_left(self) -> float:
        return self.pump.max_boost_minutes_per_day - self._minutes_used

    # --- transitions ------------------------------------------------------
    def _restore(self, now: datetime, reason: str, cooldown: bool = True) -> PumpAction:
        self.state = PumpState.COOLDOWN if cooldown else PumpState.IDLE
        self.cooldown_until = now + timedelta(minutes=self.pump.cooldown_minutes) if cooldown else None
        self.boost_until = None
        self.extensions = 0
        self._high_streak = 0
        return PumpAction(ActionKind.RESTORE, reason)

    def _boost_minutes(self) -> int:
        return int(max(0, min(self.pump.boost_minutes, self._budget_left())))

    def tick(
        self,
        reading: DebrisReading,
        now: datetime,
        swimmers_present: bool = False,
        fresh: bool = True,
    ) -> PumpAction:
        """Advance the policy. Pass fresh=False when re-using an old reading just to
        let time pass (ending boosts, cooldowns) without counting it as a new sighting."""
        self._roll_day(now)
        self._account(now)

        high = reading.is_high(self.debris)
        if fresh:
            self._high_streak = self._high_streak + 1 if high else 0
        blocked_hours = any(w.contains(now.time()) for w in self.pump.no_boost_hours)

        if self.state is PumpState.BOOSTING:
            if swimmers_present:
                return self._restore(now, "swimmers in the water", cooldown=False)
            if blocked_hours:
                return self._restore(now, "entered no-boost hours", cooldown=False)
            if self._budget_left() <= 0:
                return self._restore(now, "daily boost budget used")
            if self.boost_until is not None and now >= self.boost_until:
                minutes = self._boost_minutes()
                if high and self.extensions < self.pump.max_extensions and minutes > 0:
                    self.extensions += 1
                    self.boost_until = now + timedelta(minutes=minutes)
                    return PumpAction(ActionKind.EXTEND,
                                      f"debris still present (extension {self.extensions})",
                                      self.pump.boost_speed_pct, minutes)
                return self._restore(now, "boost finished")
            return PumpAction(ActionKind.NONE, "boosting")

        if self.state is PumpState.COOLDOWN:
            if self.cooldown_until is not None and now < self.cooldown_until:
                return PumpAction(ActionKind.NONE, "cooldown")
            self.state = PumpState.IDLE
            self.cooldown_until = None

        # IDLE
        if not high:
            return PumpAction(ActionKind.NONE, "surface clear")
        if self._high_streak < self.debris.consecutive_readings:
            return PumpAction(ActionKind.NONE,
                              f"debris seen ({self._high_streak}/{self.debris.consecutive_readings})")
        if swimmers_present:
            return PumpAction(ActionKind.NONE, "debris high but swimmers present")
        if blocked_hours:
            return PumpAction(ActionKind.NONE, "debris high but in no-boost hours")
        minutes = self._boost_minutes()
        if minutes <= 0:
            return PumpAction(ActionKind.NONE, "debris high but daily budget used")

        self.state = PumpState.BOOSTING
        self.boost_until = now + timedelta(minutes=minutes)
        self.extensions = 0
        return PumpAction(ActionKind.BOOST,
                          f"{reading.count} debris items, {reading.coverage_pct}% coverage",
                          self.pump.boost_speed_pct, minutes)
