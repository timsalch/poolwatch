from datetime import datetime

from poolwatch.config import DebrisConfig, PumpPolicyConfig
from poolwatch.debris import ActionKind, DebrisReading, PumpAction, PumpState
from poolwatch.summary import clock, describe

from conftest import at

D, P = DebrisConfig(), PumpPolicyConfig()


def say(action, reading=DebrisReading(0, 0.0), **kw):
    args = dict(streak=0, debris=D, pump=P, state=PumpState.IDLE, boost_until=None,
                cooldown_until=None, extensions=0)
    args.update(kw)
    return describe(action, reading, **args)


def test_clock_has_no_leading_zero():
    assert clock(at(9, 5)) == "9:05 AM" and clock(at(15, 30)) == "3:30 PM"


def test_clear_surface():
    s = say(PumpAction(ActionKind.NONE, "surface clear"))
    assert s.tone == "ok" and "no debris" in s.detail


def test_waiting_for_confirmation_explains_why():
    s = say(PumpAction(ActionKind.NONE, "debris seen (1/2)"), DebrisReading(1, 0.2), streak=1)
    assert s.tone == "watch" and "1 debris item" in s.detail and "glare" in s.detail


def test_restore_while_still_dirty_is_a_warning():
    s = say(PumpAction(ActionKind.RESTORE, "boost finished"), DebrisReading(3, 0.5),
            state=PumpState.COOLDOWN, cooldown_until=at(13))
    assert s.tone == "warn" and "max extensions" in s.headline and "1:00 PM" in s.detail


def test_blocked_boost_says_why():
    s = say(PumpAction(ActionKind.NONE, "debris high but swimmers present"), DebrisReading(2, 0.4))
    assert s.tone == "warn" and "swimmers present" in s.detail


def test_early_end_names_the_reason():
    s = say(PumpAction(ActionKind.RESTORE, "swimmers in the water"))
    assert s.tone == "warn" and "swimmers in the water" in s.detail
