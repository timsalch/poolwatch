"""Process images as they land in a folder, or replay a folder of saved images as a demo."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

IMAGE_PATTERNS = ("*.jpg", "*.jpeg", "*.png")


def list_images(folders: list[Path]) -> list[Path]:
    """Every image in the folders, oldest first (by modification time, then name)."""
    found = {p for folder in folders for pattern in IMAGE_PATTERNS for p in folder.glob(pattern)}
    return sorted(found, key=lambda p: (p.stat().st_mtime, p.name))


class FolderWatcher:
    """Reports images that appear in any of the folders after it was created.

    A file modified in the last `settle_seconds` is skipped until a later poll,
    so a frame still being written by the collector isn't read half-finished.
    """

    def __init__(self, folders: list[Path], include_existing: bool = False,
                 settle_seconds: float = 1.0, clock=time.time) -> None:
        self.folders = [Path(f) for f in folders]
        self.settle = settle_seconds
        self.clock = clock
        self.seen: set[Path] = set()
        if not include_existing:
            self.seen.update(list_images(self.folders))

    def poll(self) -> list[Path]:
        cutoff = self.clock() - self.settle
        new = [p for p in list_images(self.folders)
               if p not in self.seen and p.stat().st_mtime <= cutoff]
        self.seen.update(new)
        return new


async def watch_loop(pipeline, watcher: FolderWatcher, tz, interval: float = 5.0,
                     stop: asyncio.Event | None = None) -> None:
    """Live mode: each new image is one batch; timers (boost end, retest) tick between."""
    log.info("watching %s for new images", ", ".join(str(f) for f in watcher.folders))
    while stop is None or not stop.is_set():
        now = datetime.now(tz)
        try:
            new = watcher.poll()
            for image in new:
                result = await pipeline.process([image], datetime.now(tz))
                log.info("%s: %s", image.name, result.summary.headline)
            if not new:
                await pipeline.tick(now)
        except Exception:
            log.exception("watch loop error")
        await asyncio.sleep(interval)


async def replay(pipeline, images: list[Path], start: datetime, step_minutes: float = 3.0,
                 delay_seconds: float = 3.0, sleep=asyncio.sleep) -> None:
    """Demo mode: feed saved images one at a time on a simulated clock.

    Each image is treated as arriving `step_minutes` after the previous one (the
    camera's snapshot cadence), so a 15-minute boost and its retest play out over a
    handful of images. `delay_seconds` is the real pause between images.
    """
    now = start
    for i, image in enumerate(images, 1):
        result = await pipeline.process([image], now)
        log.info("[%d/%d] %s %s: %s", i, len(images), now.strftime("%H:%M"), image.name,
                 result.summary.headline)
        await sleep(delay_seconds)
        now += timedelta(minutes=step_minutes)
    # Let any boost still running finish on the simulated clock so the demo ends settled.
    for _ in range(int(pipeline.config.pump.boost_minutes // max(step_minutes, 1)) + 1):
        if pipeline.policy.boost_until is None:
            break
        result = await pipeline.tick(now)
        if result.action.kind.value != "none":
            log.info("%s timer: %s", now.strftime("%H:%M"), result.summary.headline)
        await sleep(delay_seconds)
        now += timedelta(minutes=step_minutes)
