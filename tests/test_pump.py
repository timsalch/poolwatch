"""OmniLogicPump adapter tests against a stand-in for the pyomnilogic_local objects."""

from types import SimpleNamespace

import pytest

from poolwatch.pump import OmniLogicPump


class FakeFilter:
    def __init__(self, name, speed=40, on=True):
        self.name, self.speed, self.is_on = name, speed, on
        self.min_percent, self.max_percent = 18, 100
        self.set_calls = []

    async def set_speed(self, speed):
        self.set_calls.append(speed)


class FakeApi:
    def __init__(self):
        self.restored = 0

    async def async_restore_idle_state(self):
        self.restored += 1


class FakeOmni:
    def __init__(self, filters):
        self._filters = filters
        self._api = FakeApi()
        self.refreshes = 0

    async def refresh(self, force=False):
        self.refreshes += 1

    @property
    def all_filters(self):
        return {i: f for i, f in enumerate(self._filters)}


def pump_with(filters, dry_run=False):
    p = OmniLogicPump("127.0.0.1", "Filter Pump", dry_run=dry_run)
    p._omni = FakeOmni(filters)
    return p


async def test_set_speed_clamps_to_pump_range():
    f = FakeFilter("Filter Pump")
    p = pump_with([FakeFilter("Spa"), f])
    await p.set_speed(150)
    await p.set_speed(5)
    assert f.set_calls == [100, 18]


async def test_dry_run_sends_nothing():
    f = FakeFilter("Filter Pump")
    p = pump_with([f], dry_run=True)
    await p.set_speed(75)
    await p.restore_schedule()
    assert f.set_calls == [] and p._omni._api.restored == 0


async def test_restore_schedule_calls_restore_idle():
    p = pump_with([FakeFilter("Filter Pump")])
    await p.restore_schedule()
    assert p._omni._api.restored == 1


async def test_unknown_filter_name_lists_options():
    p = pump_with([FakeFilter("Pool Filter"), FakeFilter("Spa")])
    with pytest.raises(LookupError, match="'Pool Filter', 'Spa'"):
        await p.set_speed(50)


async def test_current_speed_zero_when_off():
    p = pump_with([FakeFilter("Filter Pump", speed=60, on=False)])
    assert await p.current_speed() == 0


def test_library_api_matches_adapter_expectations():
    """Guard against python-omnilogic-local renaming the methods the adapter uses."""
    pytest.importorskip("pyomnilogic_local")
    from pyomnilogic_local.api.api import OmniLogicAPI
    from pyomnilogic_local.filter import Filter
    from pyomnilogic_local.omnilogic import OmniLogic

    assert hasattr(OmniLogicAPI, "async_restore_idle_state")
    for attr in ("set_speed", "min_percent", "max_percent", "speed", "is_on"):
        assert hasattr(Filter, attr)
    assert hasattr(OmniLogic, "all_filters") and hasattr(OmniLogic, "refresh")
