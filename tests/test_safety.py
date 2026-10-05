from poolwatch.safety import AlertThrottle, Severity, evaluate

from conftest import at, person


def run(cfg, dets, when):
    return evaluate(dets, when, cfg.safety, cfg.water_zone, cfg.deck_zone, cfg.gate_zone)


def test_empty_scene_no_alert(cfg):
    assert run(cfg, [], at(14)) is None


def test_supervised_swim_daytime_no_alert(cfg):
    # One in the water, one on the deck at 2pm.
    assert run(cfg, [person(150, 150), person(50, 50)], at(14)) is None


def test_unsupervised_swimmer_is_critical(cfg):
    alert = run(cfg, [person(150, 150)], at(14))
    assert alert.severity is Severity.CRITICAL
    assert alert.rule == "in_water_unsupervised"


def test_swimmer_in_quiet_hours_is_critical_even_with_adult(cfg):
    alert = run(cfg, [person(150, 150), person(50, 50)], at(22))
    assert alert.rule == "in_water_quiet_hours"
    assert alert.people_on_deck == 1


def test_quiet_hours_cross_midnight(cfg):
    assert run(cfg, [person(150, 150), person(50, 50)], at(3)).rule == "in_water_quiet_hours"


def test_gate_at_night_is_warning(cfg):
    alert = run(cfg, [person(450, 50)], at(23))
    assert alert.severity is Severity.WARNING and alert.rule == "gate_quiet_hours"


def test_gate_daytime_no_alert(cfg):
    assert run(cfg, [person(450, 50)], at(13)) is None


def test_low_confidence_person_ignored(cfg):
    assert run(cfg, [person(150, 150, conf=0.2)], at(14)) is None


def test_person_outside_all_zones_not_counted_as_supervisor(cfg):
    # Someone visible far from the deck (e.g. through a window) doesn't count.
    alert = run(cfg, [person(150, 150), person(800, 800)], at(14))
    assert alert.rule == "in_water_unsupervised"


def test_no_deck_zone_means_anyone_outside_water_supervises(raw):
    from poolwatch.config import config_from_dict
    del raw["zones"]["deck"]
    cfg = config_from_dict(raw)
    assert run(cfg, [person(150, 150), person(800, 800)], at(14)) is None


def test_throttle_suppresses_repeats_but_allows_escalation(cfg):
    throttle = AlertThrottle(120)
    warn = run(cfg, [person(450, 50)], at(23))
    assert throttle.should_send(warn, at(23))
    assert not throttle.should_send(warn, at(23, 1))
    assert throttle.should_send(warn, at(23, 3))  # cooldown elapsed

    crit = run(cfg, [person(150, 150)], at(23))
    assert throttle.should_send(crit, at(23, 4))
    assert not throttle.should_send(crit, at(23, 5))
