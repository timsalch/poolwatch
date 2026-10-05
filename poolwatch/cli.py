"""Command-line entry point: `poolwatch <command>`."""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import load_config

log = logging.getLogger("poolwatch")


async def _collect(cfg, hours: float) -> None:
    """Data collection only: save snapshots and motion frames for labeling."""
    from .ring_source import RingSource

    ring = RingSource(cfg.ring.camera_name, cfg.ring.token_file, cfg.data_dir)
    try:
        await ring.connect()
        await _collect_loop(cfg, ring, hours)
    finally:
        await ring.close()


async def _collect_loop(cfg, ring, hours: float) -> None:
    end = datetime.now() + timedelta(hours=hours)
    next_snap = datetime.now()
    refusals = 0
    while datetime.now() < end:
        try:
            if datetime.now() >= next_snap:
                path = await ring.snapshot()
                if path:
                    refusals = 0
                    log.info("snapshot: %s", path)
                else:
                    refusals += 1
                    if refusals == 1:
                        log.warning("No snapshot available from the camera. Check that "
                                    "Snapshot Capture is on for it in the Ring app "
                                    "(Device Settings > Snapshot Capture).")
                next_snap = datetime.now() + timedelta(minutes=cfg.ring.snapshot_interval_minutes)
            for frame in await ring.new_motion_frames():
                log.info("motion frame: %s", frame)
        except Exception:
            # Ring or network hiccup: log it and keep collecting.
            log.exception("collection error; retrying next cycle")
        await asyncio.sleep(60)


async def _backfill(cfg, days: int, max_clips: int) -> None:
    from .ring_source import RingSource

    ring = RingSource(cfg.ring.camera_name, cfg.ring.token_file, cfg.data_dir)
    try:
        await ring.connect()
        log.info("downloading motion clips from the last %d days (up to %d)...", days, max_clips)
        clips, frames = await ring.backfill(days, max_clips=max_clips)
        log.info("done: %d new clips, %d frames in %s", clips, frames,
                 Path(cfg.data_dir) / "frames")
    finally:
        await ring.close()


async def _run(cfg, dry_run: bool) -> None:
    from .ring_source import RingSource

    tz = ZoneInfo(cfg.timezone)
    ring = RingSource(cfg.ring.camera_name, cfg.ring.token_file, cfg.data_dir)
    try:
        await ring.connect()
        await ring.prime()
        await _run_loop(cfg, ring, dry_run, tz)
    finally:
        await ring.close()


async def _run_loop(cfg, ring, dry_run: bool, tz) -> None:
    from .notify import build_notifier
    from .pipeline import DecisionLog, Pipeline
    from .pump import OmniLogicPump
    from .roboflow_client import RoboflowWorkflowDetector

    pump = OmniLogicPump(cfg.omnilogic.host, cfg.omnilogic.filter_name,
                         dry_run=dry_run or cfg.omnilogic.dry_run,
                         filter_system_id=cfg.omnilogic.filter_system_id)
    notifier = build_notifier(cfg.notify)
    pipeline = Pipeline(cfg, RoboflowWorkflowDetector(cfg.roboflow, reference_size=cfg.zone_image_size), pump, notifier,
                        DecisionLog(Path(cfg.data_dir) / "decisions.jsonl"),
                        away_source=_away_source(cfg, tz))

    next_snap = datetime.now(tz)
    log.info("running (pump %s)", "DRY RUN" if pump.dry_run else "LIVE")
    while True:
        now = datetime.now(tz)
        try:
            frames = await ring.new_motion_frames()
            if now >= next_snap:
                snap = await ring.snapshot()
                if snap:
                    frames.append(snap)
                next_snap = now + timedelta(minutes=cfg.ring.snapshot_interval_minutes)
            result = await (pipeline.process(frames, now) if frames else pipeline.tick(now))
            if frames:
                log.info("frames=%d debris=%d swimmers=%d action=%s",
                         result.frames, result.reading.count, result.swimmers,
                         result.action.kind.value)
        except Exception:
            log.exception("loop error")
        await asyncio.sleep(60)


def _test_alert(cfg) -> None:
    from .notify import build_notifier
    from .safety import Alert, Severity

    notifier = build_notifier(cfg.notify)
    names = [type(n).__name__.replace("Notifier", "") for n in notifier.notifiers]
    print("Sending a test alert via:", ", ".join(names))
    frames = sorted(Path(cfg.data_dir, "frames").glob("*.jpg"))
    image = frames[-1] if frames else None
    notifier.send(Alert(Severity.CRITICAL, "test_alert",
                        "Test alert from poolwatch. If you got this, alerts work.", 0, 0), image)
    print("Done. Check your phone and inbox." if len(names) > 1 else
          "Only the console is set up. Add ntfy_topic and/or email settings under [notify].")


def _away_source(cfg, tz):
    """Calendar-driven away detection, or None when not configured."""
    import os
    from .away import CalendarAwaySource

    if not cfg.away.enabled:
        return None
    url = os.environ.get(cfg.away.ics_url_env)
    if not url:
        log.warning("away.enabled is true but %s is not set; away alerts are off",
                    cfg.away.ics_url_env)
        return None
    return CalendarAwaySource(url, cfg.away, tz)


def _away_status(cfg) -> None:
    tz = ZoneInfo(cfg.timezone)
    source = _away_source(cfg, tz)
    if source is None:
        print(f"Away mode is off. Set [away] enabled = true and the {cfg.away.ics_url_env} "
              "environment variable to your calendar's secret iCal address.")
        return
    source.refresh(force=True)
    now = datetime.now(tz)
    current = source.current(now)
    print(f"Now: {'AWAY (' + current.reason + ')' if current else 'home'}")
    upcoming = [w for w in source.windows() if w.end > now]
    if not upcoming:
        print("No current or upcoming trips found on the calendar.")
    for w in upcoming:
        print(f"  {w.start:%a %b %d %H:%M} -> {w.end:%a %b %d %H:%M}  {w.reason}")


async def _pump_info(cfg) -> None:
    from .pump import OmniLogicPump

    for line in await OmniLogicPump(cfg.omnilogic.host).describe():
        print(line)
    print("\nTip: turn one pump on in the OmniLogic app and run this again; "
          "the one showing on=True is that pump. Put its id in omnilogic.filter_system_id.")


def _detect(cfg, image: str) -> None:
    from .roboflow_client import RoboflowWorkflowDetector
    from .debris import score_debris
    from .safety import evaluate

    dets = RoboflowWorkflowDetector(cfg.roboflow, reference_size=cfg.zone_image_size).detect(image)
    for d in dets:
        print(f"{d.label:10s} {d.confidence:.2f} at ({d.x:.0f},{d.y:.0f}) "
              f"in_water={d.in_zone(cfg.water_zone)}")
    now = datetime.now(ZoneInfo(cfg.timezone))
    print("debris:", score_debris(dets, cfg.water_zone, cfg.debris))
    print("alert:", evaluate(dets, now, cfg.safety, cfg.water_zone, cfg.deck_zone, cfg.gate_zone))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="poolwatch")
    parser.add_argument("-c", "--config", default="config.toml")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ring-login", help="one-time Ring login, saves a refresh token")
    p = sub.add_parser("collect", help="save snapshots and motion frames for labeling")
    p.add_argument("--hours", type=float, default=24)
    p = sub.add_parser("backfill", help="download past motion clips for training data")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--max-clips", type=int, default=500)
    sub.add_parser("pump-info", help="list filters on the OmniLogic controller")
    sub.add_parser("test-alert", help="send a test alert to every configured channel")
    sub.add_parser("away", help="show whether the calendar says you're away, and upcoming trips")
    p = sub.add_parser("detect", help="run the Workflow on one image and show results")
    p.add_argument("image")
    p = sub.add_parser("run", help="run the monitor loop")
    p.add_argument("--dry-run", action="store_true", help="never send pump commands")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.cmd == "ring-login":
        from .ring_source import ring_login
        import tomllib
        token_file = ".ring_token.json"
        if Path(args.config).exists():
            with open(args.config, "rb") as fh:
                token_file = tomllib.load(fh).get("ring", {}).get("token_file", token_file)
        asyncio.run(ring_login(token_file))
        return

    try:
        _dispatch(args)
    except KeyboardInterrupt:
        log.info("stopped")


def _dispatch(args) -> None:
    cfg = load_config(args.config)
    if args.cmd == "collect":
        asyncio.run(_collect(cfg, args.hours))
    elif args.cmd == "backfill":
        asyncio.run(_backfill(cfg, args.days, args.max_clips))
    elif args.cmd == "test-alert":
        _test_alert(cfg)
    elif args.cmd == "away":
        _away_status(cfg)
    elif args.cmd == "pump-info":
        asyncio.run(_pump_info(cfg))
    elif args.cmd == "detect":
        _detect(cfg, args.image)
    elif args.cmd == "run":
        asyncio.run(_run(cfg, args.dry_run))


if __name__ == "__main__":
    main()
