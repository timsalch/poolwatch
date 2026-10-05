"""Away-from-home detection from a calendar (Google Calendar's secret iCal link).

A trip is inferred from flight events: away starts when an outbound flight
departs and ends when the next flight home lands. Google adds flights it finds
in Gmail to the calendar automatically (titles like "Flight to Denver (UA 1234)").
Events whose title contains an away keyword ("Vacation", "Out of town") also
count, which doubles as a manual override: add an all-day event to go into away mode.

Only the standard library is used; recurring events (RRULE) are not expanded.
"""

from __future__ import annotations

import logging
import re
import time
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import AwayConfig

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CalEvent:
    summary: str
    start: datetime
    end: datetime
    location: str = ""
    description: str = ""


@dataclass(frozen=True)
class AwayWindow:
    start: datetime
    end: datetime
    reason: str

    def contains(self, t: datetime) -> bool:
        return self.start <= t < self.end


# --- iCal parsing ---------------------------------------------------------

def _unfold(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _unescape(value: str) -> str:
    return (value.replace("\\n", "\n").replace("\\N", "\n").replace("\\,", ",")
            .replace("\\;", ";").replace("\\\\", "\\"))


def _parse_dt(value: str, params: dict[str, str], default_tz: ZoneInfo) -> datetime:
    if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", value):
        d = datetime.strptime(value, "%Y%m%d")
        return d.replace(tzinfo=default_tz)
    utc = value.endswith("Z")
    d = datetime.strptime(value.rstrip("Z"), "%Y%m%dT%H%M%S")
    if utc:
        return d.replace(tzinfo=timezone.utc)
    tzid = params.get("TZID")
    if tzid:
        try:
            return d.replace(tzinfo=ZoneInfo(tzid))
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return d.replace(tzinfo=default_tz)


def parse_ics(text: str, default_tz: ZoneInfo) -> list[CalEvent]:
    events: list[CalEvent] = []
    current: dict | None = None
    for line in _unfold(text):
        if line == "BEGIN:VEVENT":
            current = {}
            continue
        if line == "END:VEVENT":
            if current is not None and "DTSTART" in current:
                start = current["DTSTART"]
                end = current.get("DTEND") or (start + timedelta(days=1)
                                               if current.get("_allday") else start)
                events.append(CalEvent(current.get("SUMMARY", ""), start, end,
                                       current.get("LOCATION", ""), current.get("DESCRIPTION", "")))
            current = None
            continue
        if current is None or ":" not in line:
            continue
        head, value = line.split(":", 1)
        name, *raw_params = head.split(";")
        params = dict(p.split("=", 1) for p in raw_params if "=" in p)
        name = name.upper()
        try:
            if name in ("DTSTART", "DTEND"):
                current[name] = _parse_dt(value, params, default_tz)
                if name == "DTSTART" and (params.get("VALUE") == "DATE" or len(value) == 8):
                    current["_allday"] = True
            elif name in ("SUMMARY", "LOCATION", "DESCRIPTION"):
                current[name] = _unescape(value)
        except ValueError:
            log.debug("skipping unparseable %s: %s", name, value)
    return events


# --- trip inference -------------------------------------------------------

_FLIGHT_TO = re.compile(r"\bflight\s+to\s+(.+?)(?:\s*\(|$)", re.IGNORECASE)


def _has_term(text: str, terms: list[str]) -> bool:
    return any(re.search(rf"\b{re.escape(t)}\b", text, re.IGNORECASE) for t in terms)


def flight_destination(event: CalEvent) -> str | None:
    """Return the destination text for a flight event, or None if it isn't a flight."""
    m = _FLIGHT_TO.search(event.summary)
    if m:
        return m.group(1).strip()
    if re.search(r"\bflight\b", event.summary, re.IGNORECASE):
        return ""  # a flight, destination unknown
    return None


def is_flight_home(event: CalEvent, home_terms: list[str]) -> bool:
    dest = flight_destination(event)
    if dest:
        return _has_term(dest, home_terms)
    # No "to <city>" in the title: look for "to <home>" in the details instead.
    details = f"{event.summary}\n{event.description}"
    return any(re.search(rf"\bto\s+(\w+\s+)?{re.escape(t)}\b", details, re.IGNORECASE)
               for t in home_terms)


def away_windows(events: list[CalEvent], config: AwayConfig) -> list[AwayWindow]:
    windows: list[AwayWindow] = []
    max_trip = timedelta(days=config.max_trip_days)

    for e in events:
        if config.keywords and _has_term(e.summary, config.keywords):
            windows.append(AwayWindow(e.start, e.end, f"calendar: {e.summary}"))

    flights = sorted((e for e in events if flight_destination(e) is not None), key=lambda e: e.start)
    trip_start: CalEvent | None = None
    for f in flights:
        home = is_flight_home(f, config.home_terms)
        if not home and trip_start is None:
            trip_start = f
        elif home and trip_start is not None:
            windows.append(AwayWindow(trip_start.start, f.end,
                                      f"trip: {trip_start.summary} until {f.summary}"))
            trip_start = None
    if trip_start is not None:
        # Outbound with no return flight on the calendar: assume a capped trip.
        windows.append(AwayWindow(trip_start.start, trip_start.start + max_trip,
                                  f"trip: {trip_start.summary} (no return flight found; "
                                  f"assuming {config.max_trip_days} days)"))
    return sorted(windows, key=lambda w: w.start)


# --- live source ----------------------------------------------------------

class CalendarAwaySource:
    """Fetches the calendar periodically and answers "is the owner away right now?"."""

    def __init__(self, url: str, config: AwayConfig, tz: ZoneInfo, fetch=None, clock=time.monotonic) -> None:
        self.url = url
        self.config = config
        self.tz = tz
        self._fetch = fetch or self._http_get
        self._clock = clock
        self._windows: list[AwayWindow] = []
        self._fetched_at: float | None = None

    @staticmethod
    def _http_get(url: str) -> str:
        with urllib.request.urlopen(url, timeout=20) as resp:
            return resp.read().decode("utf-8", errors="replace")

    def refresh(self, force: bool = False) -> None:
        now = self._clock()
        due = self._fetched_at is None or now - self._fetched_at >= self.config.refresh_minutes * 60
        if not (force or due):
            return
        try:
            events = parse_ics(self._fetch(self.url), self.tz)
            self._windows = away_windows(events, self.config)
            self._fetched_at = now
        except Exception as exc:
            # Keep the last good answer; try again next cycle.
            log.warning("could not read calendar (%s); keeping last known trips", exc)
            if self._fetched_at is None:
                self._fetched_at = now - self.config.refresh_minutes * 60 + 300  # retry in 5 min

    def windows(self) -> list[AwayWindow]:
        self.refresh()
        return list(self._windows)

    def current(self, t: datetime) -> AwayWindow | None:
        for w in self.windows():
            if w.contains(t):
                return w
        return None

    def is_away(self, t: datetime) -> bool:
        return self.current(t) is not None
