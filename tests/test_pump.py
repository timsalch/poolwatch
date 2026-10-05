"""OmniLogicPump adapter tests against a stand-in for the pyomnilogic_local objects."""

from types import SimpleNamespace

import pytest

from poolwatch.pump import OmniLogicPump

_next_id = iter(range(100, 1000))


class FakeFilter:
    def __init__(self, name, speed=40, on=True, system_id=None, min_pct=18):
        self.name, self.speed, self.is_on = name, speed, on
        self.system_id = system_id if system_id is not None else next(_next_id)
        self.min_percent, self.max_percent = min_pct, 100
        self.set_calls = []

    async def set_speed(self, speed):
        self.set_calls.append(speed)


class FakeApi:
    def __init__(self):
        self.restored = 0

    async def async_restore_idle_state(self):
        self.restored += 1


def _bow(name, filters):
    return SimpleNamespace(name=name, filters={f.system_id: f for f in filters})


class FakeOmni:
    """Mirrors omni.backyard.bow[...].filters[...] from python-omnilogic-local."""

    def __init__(self, bows):
        self.backyard = SimpleNamespace(bow={i: b for i, b in enumerate(bows)})
        self._api = FakeApi()
        self.refreshes = 0

    async def refresh(self, force=False):
        self.refreshes += 1


def pump_with(filters, dry_run=False, name="Filter Pump", system_id=None, bows=None):
    p = OmniLogicPump("127.0.0.1", name, dry_run=dry_run, filter_system_id=system_id)
    p._omni = FakeOmni(bows if bows is not None else [_bow("Pool", filters)])
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
    p = pump_with([FakeFilter("Pool Filter", system_id=3), FakeFilter("Spa", system_id=4)])
    with pytest.raises(LookupError, match="id=3 name='Pool Filter'.*id=4 name='Spa'"):
        await p.set_speed(50)


async def test_duplicate_names_refuse_to_guess():
    # Tim's controller: two pumps both named "Filter Pump".
    a = FakeFilter("Filter Pump", system_id=7)
    b = FakeFilter("Filter Pump", system_id=12, min_pct=40)
    p = pump_with([a, b])
    with pytest.raises(LookupError, match="2 filter pumps match.*filter_system_id"):
        await p.set_speed(75)
    assert a.set_calls == [] and b.set_calls == []


async def test_select_by_system_id_with_duplicate_names():
    a = FakeFilter("Filter Pump", system_id=7)
    b = FakeFilter("Filter Pump", system_id=12, min_pct=40)
    p = pump_with([a, b], system_id=12)
    await p.set_speed(30)
    assert a.set_calls == [] and b.set_calls == [40]  # clamped to that pump's own minimum


async def test_unknown_system_id():
    p = pump_with([FakeFilter("Filter Pump", system_id=7)], system_id=99)
    with pytest.raises(LookupError, match="No filter pump matching filter id 99"):
        await p.set_speed(50)


async def test_filters_across_bodies_of_water():
    pool = FakeFilter("Filter Pump", system_id=7)
    feature = FakeFilter("Filter Pump", system_id=12, min_pct=40, on=False)
    p = pump_with([], bows=[_bow("Pool", [pool]), _bow("Water Feature", [feature])])
    lines = await p.describe()
    assert len(lines) == 2
    assert "id=7" in lines[0] and "body='Pool'" in lines[0] and "on=True" in lines[0]
    assert "id=12" in lines[1] and "body='Water Feature'" in lines[1] and "range=40-100%" in lines[1]


async def test_current_speed_zero_when_off():
    p = pump_with([FakeFilter("Filter Pump", speed=60, on=False)])
    assert await p.current_speed() == 0


def test_library_api_matches_adapter_expectations():
    """Guard against python-omnilogic-local renaming what the adapter relies on."""
    pytest.importorskip("pyomnilogic_local")
    from pyomnilogic_local._base import OmniEquipment
    from pyomnilogic_local.api.api import OmniLogicAPI
    from pyomnilogic_local.backyard import Backyard
    from pyomnilogic_local.bow import Bow
    from pyomnilogic_local.filter import Filter
    from pyomnilogic_local.omnilogic import OmniLogic

    assert hasattr(OmniLogicAPI, "async_restore_idle_state")
    for attr in ("set_speed", "min_percent", "max_percent", "speed", "is_on"):
        assert hasattr(Filter, attr)
    for attr in ("system_id", "name", "bow_id"):
        assert hasattr(OmniEquipment, attr)
    assert issubclass(Bow, OmniEquipment)
    assert hasattr(OmniLogic, "refresh")
    # backyard.bow and bow.filters are set in __init__, so check the source for them.
    import inspect
    assert "self.bow" in inspect.getsource(Backyard)
    assert "self.filters" in inspect.getsource(Bow)
