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
    await ring.connect()
    end = datetime.now() + timedelta(hours=hours)
    next_snap = datetime.now()
    while datetime.now() < end:
        if datetime.now() >= next_snap:
            path = await ring.snapshot()
            log.info("snapshot: %s", path or "camera refused (battery model?)")
            next_snap = datetime.now() + timedelta(minutes=cfg.ring.snapshot_interval_minutes)
        for frame in await ring.new_motion_frames():
            log.info("motion frame: %s", frame)
        await asyncio.sleep(60)


async def _run(cfg, dry_run: bool) -> None:
    from .notify import ConsoleNotifier, NtfyNotifier
    from .pipeline import DecisionLog, Pipeline
    from .pump import OmniLogicPump
    from .ring_source import RingSource
    from .roboflow_client import RoboflowWorkflowDetector

    tz = ZoneInfo(cfg.timezone)
    ring = RingSource(cfg.ring.camera_name, cfg.ring.token_file, cfg.data_dir)
    await ring.connect()
    await ring.prime()
    pump = OmniLogicPump(cfg.omnilogic.host, cfg.omnilogic.filter_name,
                         dry_run=dry_run or cfg.omnilogic.dry_run,
                         filter_system_id=cfg.omnilogic.filter_system_id)
    notifier = (NtfyNotifier(cfg.notify.ntfy_topic, cfg.notify.ntfy_server)
                if cfg.notify.ntfy_topic else ConsoleNotifier())
    pipeline = Pipeline(cfg, RoboflowWorkflowDetector(cfg.roboflow), pump, notifier,
                        DecisionLog(Path(cfg.data_dir) / "decisions.jsonl"))

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

    dets = RoboflowWorkflowDetector(cfg.roboflow).detect(image)
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
    sub.add_parser("pump-info", help="list filters on the OmniLogic controller")
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

    cfg = load_config(args.config)
    if args.cmd == "collect":
        asyncio.run(_collect(cfg, args.hours))
    elif args.cmd == "pump-info":
        asyncio.run(_pump_info(cfg))
    elif args.cmd == "detect":
        _detect(cfg, args.image)
    elif args.cmd == "run":
        asyncio.run(_run(cfg, args.dry_run))


if __name__ == "__main__":
    main()
