"""Stuck debris: pieces that survive a full boost cycle get ignored until they're gone.

Some "debris" won't clear no matter how long the pump runs: a leaf caught on a
step, a stain, or a reflection the model keeps calling debris. Without this, the
pump would boost, give up, cool down and boost again all day. Pure logic, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .config import DebrisConfig
from .detections import Detection
from .geometry import Zone


@dataclass
class StuckSpot:
    x: float
    y: float
    width: float
    height: float
    since: datetime
    clear_streak: int = 0  # consecutive images with nothing at this spot

    def matches(self, d: Detection, radius: float) -> bool:
        return (abs(d.x - self.x) <= self.width / 2 + radius
                and abs(d.y - self.y) <= self.height / 2 + radius)


class StuckDebris:
    def __init__(self, config: DebrisConfig, water: Zone) -> None:
        self.config = config
        xs = [x for x, _ in water.points]
        self.radius = (max(xs) - min(xs)) * config.stuck_match_radius_pct / 100.0
        self.spots: list[StuckSpot] = []

    def is_ignored(self, d: Detection) -> bool:
        return any(s.matches(d, self.radius) for s in self.spots)

    def mark(self, debris: list[Detection], now: datetime) -> int:
        """Ignore these pieces from now on. Returns how many new spots were added."""
        added = 0
        for d in debris:
            if not self.is_ignored(d):
                self.spots.append(StuckSpot(d.x, d.y, d.width, d.height, now))
                added += 1
        return added

    def observe(self, debris: list[Detection]) -> list[StuckSpot]:
        """Update with a fresh image's debris on the water; returns spots now released."""
        released = []
        for spot in list(self.spots):
            seen = [d for d in debris if spot.matches(d, self.radius)]
            if seen:
                spot.clear_streak = 0
                # Follow a slowly drifting piece so it stays matched.
                nearest = min(seen, key=lambda d: (d.x - spot.x) ** 2 + (d.y - spot.y) ** 2)
                spot.x, spot.y = nearest.x, nearest.y
            else:
                spot.clear_streak += 1
                if spot.clear_streak >= self.config.stuck_clear_readings:
                    self.spots.remove(spot)
                    released.append(spot)
        return released
