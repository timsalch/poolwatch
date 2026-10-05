"""Configuration loaded from a TOML file (see config.example.toml)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path

from .geometry import Zone


@dataclass(frozen=True)
class TimeWindow:
    """A daily window like 22:00-07:00. Windows may cross midnight."""

    start: time
    end: time

    @classmethod
    def parse(cls, text: str) -> "TimeWindow":
        try:
            a, b = (part.strip() for part in text.split("-"))
            return cls(time.fromisoformat(a), time.fromisoformat(b))
        except ValueError as exc:
            raise ValueError(f"Bad time window '{text}', expected HH:MM-HH:MM") from exc

    def contains(self, t: time) -> bool:
        if self.start == self.end:
            return False
        if self.start < self.end:
            return self.start <= t < self.end
        return t >= self.start or t < self.end


@dataclass(frozen=True)
class SafetyConfig:
    person_labels: tuple[str, ...] = ("person",)
    min_confidence: float = 0.5
    # Times nobody should be in the pool (any swimmer is an alert).
    quiet_hours: tuple[TimeWindow, ...] = ()
    alert_cooldown_seconds: int = 120


@dataclass(frozen=True)
class DebrisConfig:
    debris_labels: tuple[str, ...] = ("leaf", "debris", "bug")
    min_confidence: float = 0.4
    # Boost when either threshold is met.
    count_threshold: int = 5
    coverage_threshold_pct: float = 1.0
    # Require N consecutive high readings before boosting (filters out glare flukes).
    consecutive_readings: int = 2


@dataclass(frozen=True)
class PumpPolicyConfig:
    boost_speed_pct: int = 75
    boost_minutes: int = 30
    max_extensions: int = 2
    cooldown_minutes: int = 60
    max_boost_minutes_per_day: int = 240
    # No boosting in these windows (noise, energy rates). Active boosts are ended.
    no_boost_hours: tuple[TimeWindow, ...] = ()


@dataclass(frozen=True)
class RingConfig:
    camera_name: str
    token_file: str = ".ring_token.json"
    snapshot_interval_minutes: int = 15


@dataclass(frozen=True)
class RoboflowConfig:
    workspace: str
    workflow_id: str
    api_url: str = "https://serverless.roboflow.com"
    api_key_env: str = "ROBOFLOW_API_KEY"
    # Inputs your Workflow declares (confidence, iou_threshold, ...), sent with every call.
    parameters: dict = field(default_factory=dict)


@dataclass(frozen=True)
class OmniLogicConfig:
    host: str
    filter_name: str | None = "Filter Pump"
    # Preferred: the unique id shown by `poolwatch pump-info`. Overrides filter_name.
    filter_system_id: int | None = None
    dry_run: bool = True


@dataclass(frozen=True)
class NotifyConfig:
    ntfy_topic: str | None = None
    ntfy_server: str = "https://ntfy.sh"


@dataclass(frozen=True)
class Config:
    timezone: str
    water_zone: Zone
    ring: RingConfig
    roboflow: RoboflowConfig
    omnilogic: OmniLogicConfig
    deck_zone: Zone | None = None
    gate_zone: Zone | None = None
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    debris: DebrisConfig = field(default_factory=DebrisConfig)
    pump: PumpPolicyConfig = field(default_factory=PumpPolicyConfig)
    notify: NotifyConfig = field(default_factory=NotifyConfig)
    data_dir: str = "data"


def _windows(raw: list[str] | None) -> tuple[TimeWindow, ...]:
    return tuple(TimeWindow.parse(w) for w in (raw or []))


def _zone(zones: dict, name: str) -> Zone | None:
    return Zone.from_list(name, zones[name]) if name in zones else None


def load_config(path: str | Path) -> Config:
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)
    return config_from_dict(raw)


def config_from_dict(raw: dict) -> Config:
    zones = raw.get("zones", {})
    if "water" not in zones:
        raise ValueError("config must define zones.water")

    safety_raw = dict(raw.get("safety", {}))
    safety_raw["quiet_hours"] = _windows(safety_raw.get("quiet_hours"))
    if "person_labels" in safety_raw:
        safety_raw["person_labels"] = tuple(safety_raw["person_labels"])

    debris_raw = dict(raw.get("debris", {}))
    if "debris_labels" in debris_raw:
        debris_raw["debris_labels"] = tuple(debris_raw["debris_labels"])

    pump_raw = dict(raw.get("pump", {}))
    pump_raw["no_boost_hours"] = _windows(pump_raw.get("no_boost_hours"))

    return Config(
        timezone=raw.get("timezone", "America/Los_Angeles"),
        water_zone=Zone.from_list("water", zones["water"], zones.get("water_exclude")),
        deck_zone=_zone(zones, "deck"),
        gate_zone=_zone(zones, "gate"),
        ring=RingConfig(**raw["ring"]),
        roboflow=RoboflowConfig(**raw["roboflow"]),
        omnilogic=OmniLogicConfig(**raw["omnilogic"]),
        safety=SafetyConfig(**safety_raw),
        debris=DebrisConfig(**debris_raw),
        pump=PumpPolicyConfig(**pump_raw),
        notify=NotifyConfig(**raw.get("notify", {})),
        data_dir=raw.get("data_dir", "data"),
    )
