from datetime import timedelta

from poolwatch.debris import ActionKind, DebrisReading, PumpPolicy, PumpState, score_debris

from conftest import at, leaf

CLEAR = DebrisReading(0, 0.0)
DIRTY = DebrisReading(6, 0.5)


def policy(cfg):
    return PumpPolicy(cfg.debris, cfg.pump)


# --- scoring --------------------------------------------------------------

def test_score_counts_only_debris_in_water(cfg):
    dets = [leaf(150, 150), leaf(160, 160), leaf(50, 50), leaf(150, 150, conf=0.1)]
    r = score_debris(dets, cfg.water_zone, cfg.debris)
    assert r.count == 2
    assert r.coverage_pct == 0.32  # 2 * 16px^2 / 10000px^2


def test_coverage_threshold_triggers_without_count(cfg):
    r = score_debris([leaf(150, 150, size=25)], cfg.water_zone, cfg.debris)
    assert r.count == 1 and r.coverage_pct == 6.25
    assert r.is_high(cfg.debris)


# --- policy -----------------------------------------------------------------

def test_needs_consecutive_readings_before_boost(cfg):
    p = policy(cfg)
    assert p.tick(DIRTY, at(12)).kind is ActionKind.NONE
    action = p.tick(DIRTY, at(12, 15))
    assert action.kind is ActionKind.BOOST
    assert action.speed_pct == 75 and action.minutes == 30
    assert p.state is PumpState.BOOSTING


def test_glare_fluke_resets_streak(cfg):
    p = policy(cfg)
    p.tick(DIRTY, at(12))
    p.tick(CLEAR, at(12, 15))
    assert p.tick(DIRTY, at(12, 30)).kind is ActionKind.NONE


def test_boost_ends_then_cooldown(cfg):
    p = policy(cfg)
    p.tick(DIRTY, at(12)); p.tick(DIRTY, at(12, 1))
    assert p.tick(CLEAR, at(12, 10)).kind is ActionKind.NONE
    assert p.tick(CLEAR, at(12, 31)).kind is ActionKind.RESTORE
    assert p.state is PumpState.COOLDOWN
    # Dirty again during cooldown: no boost.
    assert p.tick(DIRTY, at(12, 40)).kind is ActionKind.NONE
    assert p.tick(DIRTY, at(12, 50)).kind is ActionKind.NONE
    # After cooldown, the streak carries and it boosts again.
    assert p.tick(DIRTY, at(13, 32)).kind is ActionKind.BOOST


def test_extension_when_still_dirty_then_capped(cfg):
    p = policy(cfg)
    p.tick(DIRTY, at(12)); p.tick(DIRTY, at(12, 1))
    ext = p.tick(DIRTY, at(12, 31))
    assert ext.kind is ActionKind.EXTEND and ext.minutes == 30
    # max_extensions=1, so the next expiry restores.
    assert p.tick(DIRTY, at(13, 1)).kind is ActionKind.RESTORE


def test_swimmers_block_and_end_boost(cfg):
    p = policy(cfg)
    p.tick(DIRTY, at(12))
    assert p.tick(DIRTY, at(12, 1), swimmers_present=True).kind is ActionKind.NONE
    assert p.tick(DIRTY, at(12, 2)).kind is ActionKind.BOOST
    a = p.tick(DIRTY, at(12, 5), swimmers_present=True)
    assert a.kind is ActionKind.RESTORE and "swimmers" in a.reason
    assert p.state is PumpState.IDLE  # no cooldown penalty for swimmers


def test_no_boost_hours(cfg):
    p = policy(cfg)
    p.tick(DIRTY, at(23)); assert p.tick(DIRTY, at(23, 15)).kind is ActionKind.NONE
    # A boost running into no-boost hours is ended.
    p2 = policy(cfg)
    p2.tick(DIRTY, at(21, 40)); p2.tick(DIRTY, at(21, 45))
    assert p2.state is PumpState.BOOSTING
    assert p2.tick(DIRTY, at(22, 1)).kind is ActionKind.RESTORE


def test_daily_budget_limits_boosting(cfg):
    # Budget is 90 minutes/day.
    p = policy(cfg)
    t = at(8)
    p.tick(DIRTY, t); p.tick(DIRTY, t + timedelta(minutes=1))       # boost 30
    t += timedelta(minutes=1)
    for _ in range(60):
        t += timedelta(minutes=1); p.tick(DIRTY, t)                   # extend once, then restore
    assert p.state is PumpState.COOLDOWN
    assert 59 <= p.minutes_used_today <= 61
    t += timedelta(minutes=61)
    p.tick(DIRTY, t)                                                  # streak restarts after restore
    t += timedelta(minutes=1)
    boost = p.tick(DIRTY, t)
    assert boost.kind is ActionKind.BOOST and boost.minutes == 30
    for _ in range(30):
        t += timedelta(minutes=1); last = p.tick(DIRTY, t)
    assert last.kind is ActionKind.RESTORE  # budget gone, no extension
    assert p.minutes_used_today >= 90
    t += timedelta(minutes=61)
    p.tick(DIRTY, t)
    t += timedelta(minutes=1)
    assert "budget" in p.tick(DIRTY, t).reason


def test_budget_resets_next_day(cfg):
    p = policy(cfg)
    p._budget_day = at(12).date()
    p._minutes_used = 90
    p.tick(DIRTY, at(12))
    assert "budget" in p.tick(DIRTY, at(12, 5)).reason
    assert p.tick(DIRTY, at(9, day=5)).kind is ActionKind.BOOST


def test_stale_ticks_do_not_count_as_sightings(cfg):
    p = policy(cfg)
    p.tick(DIRTY, at(12))
    assert p.tick(DIRTY, at(12, 1), fresh=False).kind is ActionKind.NONE
    assert p._high_streak == 1


def test_stale_tick_still_ends_boost(cfg):
    p = policy(cfg)
    p.tick(DIRTY, at(12)); p.tick(DIRTY, at(12, 1))
    assert p.tick(CLEAR, at(12, 31), fresh=False).kind is ActionKind.RESTORE
