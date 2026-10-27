from datetime import datetime

from poolwatch.history import EventStore

from conftest import at


def test_ids_continue_across_restarts(tmp_path):
    s = EventStore(tmp_path)
    assert s.append({"ts": at(12)})["id"] == 1
    assert s.append({"ts": at(12, 1)})["id"] == 2
    assert EventStore(tmp_path).append({"ts": at(12, 2)})["id"] == 3


def test_recent_is_newest_first_and_neighbors(tmp_path):
    s = EventStore(tmp_path)
    for i in range(5):
        s.append({"n": i})
    assert [e["n"] for e in s.recent(3)] == [4, 3, 2]
    assert s.get(3)["n"] == 2 and s.get(99) is None
    assert s.neighbors(3) == (2, 4)
    assert s.neighbors(1) == (None, 2) and s.neighbors(5) == (4, None)
    assert s.neighbors(42) == (None, None)


def test_partial_line_is_skipped(tmp_path):
    s = EventStore(tmp_path)
    s.append({"n": 1})
    with s.events_path.open("a") as fh:
        fh.write('{"id": 2, "n"')  # writer mid-line
    assert [e["n"] for e in s.recent(10)] == [1]


def test_state_round_trip_and_reset(tmp_path):
    s = EventStore(tmp_path)
    assert s.state() is None
    s.write_state({"now": at(12), "x": (1, 2)})
    assert s.state() == {"now": "2026-10-04T12:00:00-07:00", "x": [1, 2]}
    s.append({"n": 1})
    s.reset()
    assert s.state() is None and s.recent() == [] and s.append({})["id"] == 1
