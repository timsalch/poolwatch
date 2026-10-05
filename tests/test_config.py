from datetime import time
from pathlib import Path

import pytest

from poolwatch.config import TimeWindow, config_from_dict, load_config


def test_time_window_same_day():
    w = TimeWindow.parse("09:00-17:00")
    assert w.contains(time(9, 0)) and w.contains(time(16, 59))
    assert not w.contains(time(17, 0)) and not w.contains(time(8, 59))


def test_time_window_crosses_midnight():
    w = TimeWindow.parse("22:00-07:00")
    assert w.contains(time(23, 30)) and w.contains(time(3, 0))
    assert not w.contains(time(12, 0)) and not w.contains(time(7, 0))


def test_time_window_bad_input():
    with pytest.raises(ValueError):
        TimeWindow.parse("10pm to 6am")


def test_config_from_dict(cfg):
    assert cfg.water_zone.area() == 10_000
    assert cfg.gate_zone is not None
    assert cfg.safety.quiet_hours[0].contains(time(23, 0))
    assert cfg.debris.count_threshold == 3
    assert cfg.omnilogic.dry_run is True  # safe default


def test_water_zone_required(raw):
    del raw["zones"]["water"]
    with pytest.raises(ValueError):
        config_from_dict(raw)


def test_example_config_loads():
    cfg = load_config(Path(__file__).parent.parent / "config.example.toml")
    assert cfg.ring.camera_name == "Backyard"
    assert cfg.omnilogic.dry_run is True
    assert cfg.gate_zone is None


def test_filter_system_id_optional(raw):
    assert config_from_dict(raw).omnilogic.filter_system_id is None
    raw["omnilogic"]["filter_system_id"] = 12
    assert config_from_dict(raw).omnilogic.filter_system_id == 12


def test_roboflow_parameters(raw):
    assert config_from_dict(raw).roboflow.parameters == {}
    raw["roboflow"]["parameters"] = {"confidence": 0.4, "class_agnostic_nms": False}
    assert config_from_dict(raw).roboflow.parameters["confidence"] == 0.4


def test_example_config_has_workflow_parameters():
    cfg = load_config(Path(__file__).parent.parent / "config.example.toml")
    assert cfg.roboflow.parameters["iou_threshold"] == 0.3


def test_water_exclude_from_config(raw):
    raw["zones"]["water_exclude"] = [[[120, 120], [140, 120], [140, 140], [120, 140]]]
    cfg = config_from_dict(raw)
    assert not cfg.water_zone.contains((130, 130))
    assert cfg.water_zone.contains((180, 180))
    assert cfg.water_zone.area() == 10_000 - 400
