"""Notification sinks."""

from __future__ import annotations

import logging
import os
import smtplib
import urllib.request
from email.message import EmailMessage
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


class EmailNotifier:
    """Email alerts with the camera frame attached, over SMTP with STARTTLS.

    For Gmail, turn on 2-step verification and create an app password
    (Google Account > Security > App passwords); a normal password won't work.
    """

    def __init__(
        self,
        to: tuple[str, ...],
        host: str,
        port: int,
        user: str,
        password: str,
        sender: str | None = None,
        min_severity: Severity = Severity.WARNING,
        smtp_factory=smtplib.SMTP,
    ) -> None:
        self.to, self.host, self.port = to, host, port
        self.user, self.password = user, password
        self.sender = sender or user
        self.min_severity = min_severity
        self._smtp = smtp_factory

    def build(self, alert: Alert, image: Path | None = None) -> EmailMessage:
        msg = EmailMessage()
        tag = {Severity.CRITICAL: "URGENT", Severity.WARNING: "Warning", Severity.INFO: "Info"}
        msg["Subject"] = f"[poolwatch] {tag[alert.severity]}: {alert.message}"
        msg["From"] = self.sender
        msg["To"] = ", ".join(self.to)
        if alert.severity is Severity.CRITICAL:
            msg["X-Priority"] = "1"
            msg["Importance"] = "high"
        has_image = image is not None and image.exists()
        body = [
            alert.message,
            "",
            f"Rule: {alert.rule.replace('_', ' ')}",
            f"People in the water: {alert.people_in_water}",
            f"People on the deck: {alert.people_on_deck}",
        ]
        if has_image:
            body += ["", "Camera frame attached."]
        msg.set_content("\n".join(body) + "\n")
        if has_image:
            msg.add_attachment(image.read_bytes(), maintype="image", subtype="jpeg",
                               filename=image.name)
        return msg

    def send(self, alert: Alert, image: Path | None = None) -> None:
        if alert.severity < self.min_severity:
            return
        try:
            with self._smtp(self.host, self.port, timeout=20) as smtp:
                smtp.starttls()
                smtp.login(self.user, self.password)
                smtp.send_message(self.build(alert, image))
        except (OSError, smtplib.SMTPException) as exc:
            log.error("email alert failed: %s", exc)


class MultiNotifier:
    """Send each alert to every configured channel; one failing doesn't stop the others."""

    def __init__(self, notifiers: list) -> None:
        self.notifiers = notifiers

    def send(self, alert: Alert, image: Path | None = None) -> None:
        for n in self.notifiers:
            try:
                n.send(alert, image)
            except Exception:
                log.exception("%s failed", type(n).__name__)


def build_notifier(cfg) -> "Notifier":
    """Console always, plus ntfy and/or email when configured."""
    notifiers: list = [ConsoleNotifier()]
    if cfg.ntfy_topic:
        notifiers.append(NtfyNotifier(cfg.ntfy_topic, cfg.ntfy_server))
    if cfg.email_to:
        password = os.environ.get(cfg.smtp_password_env)
        if not cfg.smtp_user or not password:
            log.warning("email alerts are configured but notify.smtp_user or the %s "
                        "environment variable is missing; email is off", cfg.smtp_password_env)
        else:
            notifiers.append(EmailNotifier(cfg.email_to, cfg.smtp_host, cfg.smtp_port,
                                           cfg.smtp_user, password, cfg.email_from,
                                           Severity[cfg.email_min_severity]))
    return MultiNotifier(notifiers)


@dataclass
class RecordingNotifier:
    sent: list[tuple[Alert, Path | None]] = field(default_factory=list)

    def send(self, alert: Alert, image: Path | None = None) -> None:
        self.sent.append((alert, image))
