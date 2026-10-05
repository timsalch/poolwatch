"""Notification sinks."""

from __future__ import annotations

import logging
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .safety import Alert, Severity

log = logging.getLogger(__name__)


class Notifier(Protocol):
    def send(self, alert: Alert, image: Path | None = None) -> None: ...


class ConsoleNotifier:
    def send(self, alert: Alert, image: Path | None = None) -> None:
        log.warning("ALERT [%s] %s (%s)", alert.severity.name, alert.message, image or "no image")


class NtfyNotifier:
    """Push to your phone with the free ntfy app (https://ntfy.sh). Pick an unguessable topic."""

    PRIORITY = {Severity.INFO: "default", Severity.WARNING: "high", Severity.CRITICAL: "urgent"}

    def __init__(self, topic: str, server: str = "https://ntfy.sh") -> None:
        self.url = f"{server.rstrip('/')}/{topic}"

    def send(self, alert: Alert, image: Path | None = None) -> None:
        headers = {
            "Title": f"Pool: {alert.rule.replace('_', ' ')}",
            "Priority": self.PRIORITY[alert.severity],
            "Tags": "warning,swimmer",
        }
        if image is not None and image.exists():
            headers["Filename"] = image.name
            headers["Message"] = alert.message
            body = image.read_bytes()
        else:
            body = alert.message.encode()
        req = urllib.request.Request(self.url, data=body, headers=headers, method="PUT")
        try:
            urllib.request.urlopen(req, timeout=10).close()
        except OSError as exc:
            log.error("ntfy send failed: %s", exc)


@dataclass
class RecordingNotifier:
    sent: list[tuple[Alert, Path | None]] = field(default_factory=list)

    def send(self, alert: Alert, image: Path | None = None) -> None:
        self.sent.append((alert, image))
