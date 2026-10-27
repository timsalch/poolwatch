"""Plain-English summaries of each pump decision, for the dashboard and logs. Pure, no I/O."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .config import DebrisConfig, PumpPolicyConfig
from .debris import ActionKind, DebrisReading, PumpAction, PumpState


@dataclass(frozen=True)
class Summary:
    headline: str
    detail: str
    # ok (clear), watch (debris seen, not confirmed), action (pump changed),
    # active (boost running), neutral (cooldown), warn (boost blocked or ended early)
    tone: str


def clock(dt: datetime | None) -> str:
    """'3:05 PM'. strftime's no-padding flags differ by OS, so strip the zero by hand."""
    if dt is None:
        return "?"
    return dt.strftime("%I:%M %p").lstrip("0")


def _debris_phrase(reading: DebrisReading) -> str:
    if reading.count == 0:
        return "no debris on the water"
    noun = "item" if reading.count == 1 else "items"
    pct = reading.coverage_pct
    shown = "<0.1" if 0 < pct < 0.1 else f"{pct:.1f}".rstrip("0").rstrip(".")
    return f"{reading.count} debris {noun} ({shown}% of the surface)"


def describe(
    action: PumpAction,
    reading: DebrisReading,
    *,
    streak: int,
    debris: DebrisConfig,
    pump: PumpPolicyConfig,
    state: PumpState,
    boost_until: datetime | None,
    cooldown_until: datetime | None,
    extensions: int,
    ignored: int = 0,
    newly_ignored: int = 0,
    released: int = 0,
    dirty_cycles: int = 0,
) -> Summary:
    base = _describe(action, reading, streak=streak, debris=debris, pump=pump, state=state,
                     boost_until=boost_until, cooldown_until=cooldown_until,
                     extensions=extensions)
    if newly_ignored:
        cycles = "cycle" if dirty_cycles == 1 else "cycles"
        noun = "item" if newly_ignored == 1 else "items"
        minutes = dirty_cycles * pump.boost_minutes * (1 + pump.max_extensions)
        return Summary(
            f"Debris won't clear: ignoring {newly_ignored} stuck {noun}",
            f"Still there after {dirty_cycles} full boost {cycles} (up to {minutes} min at "
            f"{pump.boost_speed_pct}%). Pump back on its schedule. Those spots are ignored "
            f"until they're clear in {debris.stuck_clear_readings} images in a row; new "
            "debris anywhere else still counts.",
            "warn",
        )
    if released and action.kind is ActionKind.NONE and state is not PumpState.BOOSTING:
        noun = "item is" if released == 1 else "items are"
        return Summary(
            f"Stuck debris gone: watching {'that spot' if released == 1 else 'those spots'} again",
            f"{released} ignored {noun} no longer seen. {base.headline}.",
            "ok" if base.tone in ("ok", "neutral") else base.tone,
        )
    if ignored:
        noun = "item" if ignored == 1 else "items"
        return Summary(base.headline, f"{base.detail} Ignoring {ignored} stuck {noun}.", base.tone)
    return base


def _describe(
    action: PumpAction,
    reading: DebrisReading,
    *,
    streak: int,
    debris: DebrisConfig,
    pump: PumpPolicyConfig,
    state: PumpState,
    boost_until: datetime | None,
    cooldown_until: datetime | None,
    extensions: int,
) -> Summary:
    high = reading.is_high(debris)
    needed = debris.consecutive_readings
    what = _debris_phrase(reading)

    if action.kind is ActionKind.BOOST:
        return Summary(
            f"Pump boosted to {action.speed_pct}% for {action.minutes} min",
            f"Debris in {needed} consecutive images: {what}. "
            f"Retest at {clock(boost_until)}.",
            "action",
        )

    if action.kind is ActionKind.EXTEND:
        return Summary(
            f"Retest: debris still there, boost extended {action.minutes} min",
            f"{what.capitalize()}. Extension {extensions} of {pump.max_extensions}. "
            f"Next retest at {clock(boost_until)}.",
            "action",
        )

    if action.kind is ActionKind.RESTORE:
        if action.reason == "boost finished":
            if high:
                return Summary(
                    "Retest: debris remains, max extensions reached",
                    f"{what.capitalize()}. Pump handed back to its normal schedule; "
                    f"cooldown until {clock(cooldown_until)}.",
                    "warn",
                )
            return Summary(
                "Retest: surface clear, pump back on schedule",
                f"Boost done and {what}. Normal schedule restored.",
                "action",
            )
        return Summary(
            "Boost ended early, pump back on schedule",
            f"Reason: {action.reason}. {what.capitalize()}.",
            "warn",
        )

    # No pump change: describe what we're waiting on.
    if state is PumpState.BOOSTING:
        return Summary(
            f"Boosting at {pump.boost_speed_pct}%, retest at {clock(boost_until)}",
            f"Latest image: {what}.",
            "active",
        )
    if action.reason == "cooldown":
        return Summary(
            f"Cooling down until {clock(cooldown_until)}",
            f"No new boosts until then. Latest image: {what}.",
            "neutral",
        )
    if action.reason == "surface clear":
        return Summary("Surface clear, no action", f"Latest image: {what}.", "ok")
    if action.reason.startswith("debris seen"):
        return Summary(
            f"Debris spotted ({streak} of {needed}), waiting to confirm",
            f"{what.capitalize()}. The pump boosts after {needed} consecutive images "
            "with debris, so glare or a passing shadow can't trigger it.",
            "watch",
        )
    return Summary(
        "Debris present, boost held",
        f"{what.capitalize()}. Held because: {action.reason}.",
        "warn",
    )
