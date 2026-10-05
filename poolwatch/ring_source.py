"""Pulls snapshots and motion-event clips from a Ring camera.

Uses the unofficial `ring_doorbell` package (Ring has no public API), so
expect occasional breakage when Ring changes things. Snapshots only work on
wired / plug-in cameras; battery cameras generally refuse on-demand snapshots.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

USER_AGENT = "poolwatch/0.1"


def extract_frames(clip: Path, out_dir: Path, count: int = 3) -> list[Path]:
    """Grab `count` evenly spaced frames from a clip using ffmpeg (if installed)."""
    if shutil.which("ffmpeg") is None:
        log.warning("ffmpeg not found; skipping frame extraction for %s", clip.name)
        return []
    out_dir.mkdir(parents=True, exist_ok=True)
    pattern = out_dir / f"{clip.stem}_%02d.jpg"
    # Ring motion clips run ~20-60s; sample at one frame every 8s up to `count`.
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-i", str(clip),
         "-vf", "fps=1/8", "-frames:v", str(count), str(pattern)],
        check=True,
    )
    return sorted(out_dir.glob(f"{clip.stem}_*.jpg"))


MAX_CLIP_ATTEMPTS = 3
NO_RECORDINGS_HINT = (
    "Ring isn't returning recordings for motion events. This usually means the "
    "camera has no Ring Protect plan (Ring only stores video with a subscription), "
    "or video recording is turned off for this camera in the Ring app."
)


class RingSource:
    def __init__(self, camera_name: str, token_file: str, data_dir: str) -> None:
        self.camera_name = camera_name
        self.token_path = Path(token_file)
        self.data_dir = Path(data_dir)
        self._ring = None
        self._camera = None
        self._seen_events: set[int] = set()
        self._attempts: dict[int, int] = {}
        self._warned_no_recordings = False
        self._consecutive_404s = 0
        self._last_snapshot_hash: str | None = None

    def _save_token(self, token: dict) -> None:
        self.token_path.write_text(json.dumps(token))
        self.token_path.chmod(0o600)

    async def connect(self) -> None:
        from ring_doorbell import Auth, Ring  # optional dependency

        if not self.token_path.exists():
            raise RuntimeError("No Ring token yet. Run `poolwatch ring-login` first.")
        auth = Auth(USER_AGENT, json.loads(self.token_path.read_text()), self._save_token)
        self._ring = Ring(auth)
        await self._ring.async_update_data()
        cams = [d for d in self._ring.video_devices() if d.name == self.camera_name]
        if not cams:
            names = ", ".join(d.name for d in self._ring.video_devices())
            raise LookupError(f"No Ring camera named {self.camera_name!r}; found: {names}")
        self._camera = cams[0]

    async def _latest_stored_snapshot(self) -> bytes | None:
        """Download whatever snapshot Ring has stored (from Snapshot Capture), without
        asking for a new one. ring_doorbell has no public method for this."""
        try:
            resp = await self._camera._ring.async_query(
                f"/clients_api/snapshots/image/{self._camera.id}"
            )
            return resp.content or None
        except Exception as exc:
            log.debug("could not fetch stored snapshot: %s", exc)
            return None

    async def snapshot(self) -> Path | None:
        """Save a still of the pool. Returns None if nothing new is available.

        Asks for a fresh snapshot, waiting up to ~20s (the library default of 3s is
        too short for many cameras). If none arrives, falls back to the latest
        snapshot Ring already has from Snapshot Capture. Identical images are skipped.
        """
        data = await self._camera.async_get_snapshot(retries=10, delay=2)
        if not data:
            data = await self._latest_stored_snapshot()
        if not data:
            return None
        digest = hashlib.sha256(data).hexdigest()
        if digest == self._last_snapshot_hash:
            log.debug("snapshot unchanged since last save; skipping")
            return None
        self._last_snapshot_hash = digest
        out = self.data_dir / "snapshots" / f"{datetime.now():%Y%m%d_%H%M%S}.jpg"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        return out

    async def new_motion_frames(self, limit: int = 10) -> list[Path]:
        """Download motion clips we haven't processed yet and return frames from them."""
        frames: list[Path] = []
        events = await self._camera.async_history(limit=limit, kind="motion")
        for event in reversed(events):
            event_id = event["id"]
            if event_id in self._seen_events:
                continue
            self._seen_events.add(event_id)
            clip = self.data_dir / "clips" / f"{event_id}.mp4"
            clip.parent.mkdir(parents=True, exist_ok=True)
            if not clip.exists():
                try:
                    await self._camera.async_recording_download(event_id, filename=str(clip))
                    self._consecutive_404s = 0
                except Exception as exc:
                    self._record_failure(event_id, exc)
                    continue
            frames.extend(extract_frames(clip, self.data_dir / "frames"))
        return frames

    def _record_failure(self, event_id: int, exc: Exception) -> None:
        """Retry a missing clip a few times (it may still be uploading), then give up."""
        attempts = self._attempts.get(event_id, 0) + 1
        self._attempts[event_id] = attempts
        if "404" in str(exc):
            self._consecutive_404s += 1
        if attempts < MAX_CLIP_ATTEMPTS:
            self._seen_events.discard(event_id)  # try again next poll
            log.debug("clip %s not ready (attempt %d): %s", event_id, attempts, exc)
            return
        log.debug("giving up on clip %s after %d attempts", event_id, attempts)
        if self._consecutive_404s >= 3 and not self._warned_no_recordings:
            log.warning(NO_RECORDINGS_HINT)
            self._warned_no_recordings = True

    async def prime(self, limit: int = 10) -> None:
        """Mark existing events as seen so startup doesn't reprocess old clips."""
        events = await self._camera.async_history(limit=limit, kind="motion")
        self._seen_events.update(e["id"] for e in events)


async def ring_login(token_file: str) -> None:
    """Interactive one-time login (handles 2FA) that saves a refresh token."""
    from getpass import getpass

    from ring_doorbell import Auth, Requires2FAError

    path = Path(token_file)

    def save(token: dict) -> None:
        path.write_text(json.dumps(token))
        path.chmod(0o600)

    auth = Auth(USER_AGENT, None, save)
    username = input("Ring email: ")
    password = getpass("Ring password: ")
    try:
        await auth.async_fetch_token(username, password)
    except Requires2FAError:
        await auth.async_fetch_token(username, password, input("2FA code: "))
    print(f"Saved Ring token to {path}")
