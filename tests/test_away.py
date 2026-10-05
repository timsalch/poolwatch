from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from poolwatch.away import (CalEvent, CalendarAwaySource, away_windows, flight_destination,
                            is_flight_home, parse_ics)
from poolwatch.config import AwayConfig, config_from_dict
from poolwatch.notify import RecordingNotifier
from poolwatch.pipeline import Pipeline
from poolwatch.pump import FakePump
from poolwatch.safety import Severity, evaluate

from conftest import TZ, at, person

CFG = AwayConfig(enabled=True, home_terms=("Los Angeles", "LAX"))

ICS = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
SUMMARY:Flight to Denver (UA 1234)
DTSTART:20261010T150000Z
DTEND:20261010T171500Z
LOCATION:Los Angeles LAX
END:VEVENT
BEGIN:VEVENT
SUMMARY:Dentist
DTSTART;TZID=America/Los_Angeles:20261012T090000
DTEND;TZID=America/Los_Angeles:20261012T100000
END:VEVENT
BEGIN:VEVENT
SUMMARY:Flight to Los Angeles (UA 5678)
DTSTART:20261014T010000Z
DTEND:20261014T030000Z
LOCATION:Denver DEN
DESCRIPTION:Your flight from Denver\\, CO to Los Angeles
 \\, CA
END:VEVENT
BEGIN:VEVENT
SUMMARY:Out of town - Havasu
DTSTART;VALUE=DATE:20261101
DTEND;VALUE=DATE:20261104
END:VEVENT
END:VCALENDAR
"""


def ev(summary, start, end, description=""):
    return CalEvent(summary, start, end, "", description)


def test_parse_ics_handles_utc_tzid_allday_and_folding():
    events = parse_ics(ICS, TZ)
    assert [e.summary for e in events][:2] == ["Flight to Denver (UA 1234)", "Dentist"]
    assert events[0].start == datetime(2026, 10, 10, 15, tzinfo=timezone.utc)
    assert events[1].start.tzinfo == ZoneInfo("America/Los_Angeles")
    assert "Los Angeles, CA" in events[2].description  # folded line + escaped comma
    assert events[3].start == datetime(2026, 11, 1, tzinfo=TZ)
    assert events[3].end == datetime(2026, 11, 4, tzinfo=TZ)


def test_flight_detection():
    assert flight_destination(ev("Flight to Denver (UA 1234)", at(8), at(9))) == "Denver"
    assert flight_destination(ev("flight to New York", at(8), at(9))) == "New York"
    assert flight_destination(ev("Dentist", at(8), at(9))) is None
    home = ev("Flight to Los Angeles (UA 5678)", at(8), at(9))
    assert is_flight_home(home, ["Los Angeles"])
    # Home city named only in the details.
    assert is_flight_home(ev("WN 2210 flight", at(8), at(9), "Phoenix to LAX"), ["LAX"])
    assert not is_flight_home(ev("Flight to Denver (UA 1)", at(8), at(9)), ["LAX"])


def test_trip_from_outbound_to_return_flight():
    windows = away_windows(parse_ics(ICS, TZ), CFG)
    trip = windows[0]
    assert trip.start == datetime(2026, 10, 10, 15, tzinfo=timezone.utc)   # outbound departs
    assert trip.end == datetime(2026, 10, 14, 3, tzinfo=timezone.utc)      # return lands
    assert trip.contains(at(12, day=12))
    assert not trip.contains(at(12, day=9))


def test_keyword_event_is_away():
    windows = away_windows(parse_ics(ICS, TZ), CFG)
    havasu = [w for w in windows if "Havasu" in w.reason][0]
    assert havasu.contains(datetime(2026, 11, 2, 12, tzinfo=TZ))
    assert not havasu.contains(datetime(2026, 11, 4, 12, tzinfo=TZ))


def test_connecting_flights_stay_one_trip():
    events = [
        ev("Flight to Dallas (AA 1)", at(8, day=10), at(11, day=10)),
        ev("Flight to Miami (AA 2)", at(13, day=10), at(17, day=10)),   # connection
        ev("Flight to Dallas (AA 3)", at(8, day=15), at(11, day=15)),
        ev("Flight to Los Angeles (AA 4)", at(13, day=15), at(15, day=15)),
    ]
    windows = away_windows(events, CFG)
    assert len(windows) == 1
    assert windows[0].start == at(8, day=10) and windows[0].end == at(15, day=15)


def test_no_return_flight_caps_trip():
    windows = away_windows([ev("Flight to Tokyo (JL 61)", at(10, day=1), at(23, day=1))],
                           AwayConfig(enabled=True, home_terms=("LAX",), max_trip_days=7))
    assert windows[0].end == at(10, day=8)
    assert "no return flight" in windows[0].reason


def test_source_refreshes_and_survives_fetch_errors():
    calls = {"n": 0}
    clock = {"t": 0.0}

    def fetch(url):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("network down")
        return ICS

    src = CalendarAwaySource("https://example/secret.ics", CFG, TZ, fetch=fetch, clock=lambda: clock["t"])
    assert src.is_away(at(12, day=12))
    assert calls["n"] == 1
    src.is_away(at(12, day=12))
    assert calls["n"] == 1                       # cached within refresh_minutes
    clock["t"] += 31 * 60
    assert src.is_away(at(12, day=12))           # fetch failed, last good answer kept
    assert calls["n"] == 2


def test_away_alert_rules(cfg):
    run = lambda dets, away: evaluate(dets, at(14), cfg.safety, cfg.water_zone,
                                      cfg.deck_zone, cfg.gate_zone, away=away)
    # Supervised daytime swim: fine at home, critical while away (e.g. a house-sitter's guests).
    assert run([person(150, 150), person(50, 50)], away=False) is None
    alert = run([person(150, 150), person(50, 50)], away=True)
    assert alert.severity is Severity.CRITICAL and alert.rule == "in_water_while_away"
    # Someone on the deck while away: a warning.
    alert = run([person(50, 50)], away=True)
    assert alert.severity is Severity.WARNING and alert.rule == "at_pool_while_away"
    assert run([], away=True) is None


async def test_pipeline_uses_away_source(cfg, tmp_path):
    class Away:
        def is_away(self, t):
            return True

    class Det:
        def detect(self, p):
            return [person(50, 50)]

    notes = RecordingNotifier()
    pipe = Pipeline(cfg, Det(), FakePump(), notes, away_source=Away())
    await pipe.process([Path("f.jpg")], at(14))
    assert notes.sent[0][0].rule == "at_pool_while_away"


def test_away_config_parsing(raw):
    assert config_from_dict(raw).away.enabled is False
    raw["away"] = {"enabled": True, "home_terms": ["Ontario", "ONT"], "max_trip_days": 10}
    away = config_from_dict(raw).away
    assert away.home_terms == ("Ontario", "ONT") and away.max_trip_days == 10
    assert "vacation" in away.keywords
