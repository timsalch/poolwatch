from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from poolwatch.config import config_from_dict
from poolwatch.detections import Detection

TZ = ZoneInfo("America/Los_Angeles")

# Water is a 100x100 square at (100..200, 100..200); deck surrounds it.
RAW = {
    "timezone": "America/Los_Angeles",
    "zones": {
        "water": [[100, 100], [200, 100], [200, 200], [100, 200]],
        "deck": [[0, 0], [300, 0], [300, 300], [0, 300]],
        "gate": [[400, 0], [500, 0], [500, 100], [400, 100]],
    },
    "ring": {"camera_name": "Backyard"},
    "roboflow": {"workspace": "ws", "workflow_id": "wf"},
    "omnilogic": {"host": "127.0.0.1"},
    "safety": {"quiet_hours": ["21:00-07:00"], "alert_cooldown_seconds": 120},
    "debris": {"count_threshold": 3, "coverage_threshold_pct": 5.0, "consecutive_readings": 2},
    "pump": {"boost_minutes": 30, "max_extensions": 1, "cooldown_minutes": 60,
             "max_boost_minutes_per_day": 90, "no_boost_hours": ["22:00-07:00"]},
}


@pytest.fixture
def raw():
    import copy
    return copy.deepcopy(RAW)


@pytest.fixture
def cfg(raw):
    return config_from_dict(raw)


def at(hour, minute=0, day=4):
    return datetime(2026, 10, day, hour, minute, tzinfo=TZ)


def person(x, y, conf=0.9, h=40):
    return Detection("person", conf, x, y, 20, h)


def leaf(x, y, conf=0.8, size=4):
    return Detection("leaf", conf, x, y, size, size)
