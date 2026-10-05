"""Person-in-pool alert rules.

This is an awareness layer. Ring motion clips and snapshots arrive with
seconds-to-minutes of delay, so this must never be the primary drowning
protection: keep the fence, self-latching gate, and a dedicated pool alarm.

We deliberately do NOT try to classify "child vs adult" from the image.
Instead we alert on situations: anyone in the water during quiet hours, or
anyone in the water with nobody else visible on the deck.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum

from .config import SafetyConfig
from .detections import Detection, filter_labels
from .geometry import Zone


class Severity(IntEnum):
    INFO = 1
    WARNING = 2
    CRITICAL = 3


@dataclass(frozen=True)
class Alert:
    severity: Severity
    rule: str
    message: str
    people_in_water: int
    people_on_deck: int


@dataclass(frozen=True)
class SceneSummary:
    in_water: list[Detection]
    on_deck: list[Detection]
    at_gate: list[Detection]


def summarize(
    detections: list[Detection],
    config: SafetyConfig,
    water: Zone,
    deck: Zone | None = None,
    gate: Zone | None = None,
) -> SceneSummary:
    people = filter_labels(detections, config.person_labels, config.min_confidence)
    in_water, on_deck, at_gate = [], [], []
    for p in people:
        # Swimmers are mostly submerged, so the visible box center is the best anchor.
        if p.in_zone(water, "center"):
            in_water.append(p)
        elif gate is not None and p.in_zone(gate, "bottom_center"):
            at_gate.append(p)
        elif deck is None or p.in_zone(deck, "bottom_center"):
            # With no deck zone configured, anyone visible outside the water counts.
            on_deck.append(p)
    return SceneSummary(in_water, on_deck, at_gate)


def evaluate(
    detections: list[Detection],
    now: datetime,
    config: SafetyConfig,
    water: Zone,
    deck: Zone | None = None,
    gate: Zone | None = None,
) -> Alert | None:
    scene = summarize(detections, config, water, deck, gate)
    n_water, n_deck = len(scene.in_water), len(scene.on_deck)
    quiet = any(w.contains(now.time()) for w in config.quiet_hours)

    if n_water and quiet:
        return Alert(Severity.CRITICAL, "in_water_quiet_hours",
                     f"{n_water} person(s) in the pool during quiet hours", n_water, n_deck)
    if n_water and n_deck == 0:
        return Alert(Severity.CRITICAL, "in_water_unsupervised",
                     f"{n_water} person(s) in the pool and nobody visible on the deck",
                     n_water, n_deck)
    if scene.at_gate and quiet:
        return Alert(Severity.WARNING, "gate_quiet_hours",
                     "Someone at the pool gate during quiet hours", n_water, n_deck)
    return None


@dataclass
class AlertThrottle:
    """Suppress repeats of the same rule within a cooldown, but always let escalations through."""

    cooldown_seconds: int
    _last: dict[str, tuple[datetime, Severity]] = field(default_factory=dict)

    def should_send(self, alert: Alert, now: datetime) -> bool:
        prev = self._last.get(alert.rule)
        if prev is not None:
            sent_at, severity = prev
            fresh = (now - sent_at).total_seconds() < self.cooldown_seconds
            if fresh and alert.severity <= severity:
                return False
        self._last[alert.rule] = (now, alert.severity)
        return True
