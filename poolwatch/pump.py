"""Filter pump controllers: a real OmniLogic adapter and an in-memory fake."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Protocol

log = logging.getLogger(__name__)


class PumpController(Protocol):
    async def set_speed(self, speed_pct: int) -> None: ...
    async def restore_schedule(self) -> None: ...
    async def current_speed(self) -> int | None: ...


@dataclass
class FakePump:
    """Records calls; used by tests and by --dry-run."""

    speed: int = 0
    calls: list[tuple[str, int | None]] = field(default_factory=list)

    async def set_speed(self, speed_pct: int) -> None:
        self.calls.append(("set_speed", speed_pct))
        self.speed = speed_pct

    async def restore_schedule(self) -> None:
        self.calls.append(("restore_schedule", None))

    async def current_speed(self) -> int | None:
        return self.speed


class OmniLogicPump:
    """Controls a Hayward OmniLogic filter pump over the local UDP API.

    Uses python-omnilogic-local. The filter pump is exposed as a "Filter"
    (not a "Pump") in that library. "Restore schedule" calls the controller's
    RestoreIdleState message, which hands control back to the programmed
    schedule, the same thing the Home Assistant "Restore Idle" button does.
    """

    def __init__(
        self,
        host: str,
        filter_name: str | None = None,
        dry_run: bool = True,
        filter_system_id: int | None = None,
    ) -> None:
        self.host = host
        self.filter_name = filter_name
        self.filter_system_id = filter_system_id
        self.dry_run = dry_run
        self._omni = None

    async def _connect(self):
        if self._omni is None:
            from pyomnilogic_local.omnilogic import OmniLogic  # optional dependency

            self._omni = OmniLogic(self.host)
        await self._omni.refresh(force=True)
        return self._omni

    @staticmethod
    def _iter_filters(omni):
        """Yield (body_of_water_name, filter) for every filter pump on the controller."""
        for bow in omni.backyard.bow.values():
            for f in bow.filters.values():
                yield bow.name, f

    @property
    def label(self) -> str:
        if self.filter_system_id is not None:
            return f"filter id {self.filter_system_id}"
        return repr(self.filter_name)

    async def _filter(self):
        omni = await self._connect()
        found = list(self._iter_filters(omni))
        if self.filter_system_id is not None:
            matches = [f for _, f in found if f.system_id == self.filter_system_id]
        else:
            matches = [f for _, f in found if f.name == self.filter_name]
        if len(matches) == 1:
            return matches[0]
        options = "; ".join(f"id={f.system_id} name={f.name!r} body={bow!r}" for bow, f in found)
        if not matches:
            raise LookupError(f"No filter pump matching {self.label}. Controller has: {options or 'none'}")
        raise LookupError(
            f"{len(matches)} filter pumps match {self.label}; set omnilogic.filter_system_id "
            f"to pick one. Controller has: {options}"
        )

    async def set_speed(self, speed_pct: int) -> None:
        f = await self._filter()
        speed = max(f.min_percent, min(f.max_percent, speed_pct))
        if self.dry_run:
            log.info("[dry-run] would set %s to %s%%", self.label, speed)
            return
        await f.set_speed(speed)

    async def restore_schedule(self) -> None:
        omni = await self._connect()
        if self.dry_run:
            log.info("[dry-run] would restore OmniLogic idle/schedule state")
            return
        # Not wrapped by the high-level OmniLogic class yet, so call the API layer.
        await omni._api.async_restore_idle_state()

    async def current_speed(self) -> int | None:
        f = await self._filter()
        return f.speed if f.is_on else 0

    async def describe(self) -> list[str]:
        """List filter pumps with their ids and body of water; use the id in config."""
        omni = await self._connect()
        return [
            f"id={f.system_id}  name={f.name!r}  body={bow!r}  on={f.is_on}  "
            f"speed={f.speed}%  range={f.min_percent}-{f.max_percent}%"
            for bow, f in self._iter_filters(omni)
        ]
