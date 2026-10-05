import smtplib

import pytest

from poolwatch.config import config_from_dict
from poolwatch.notify import (ConsoleNotifier, EmailNotifier, MultiNotifier, NtfyNotifier,
                              RecordingNotifier, build_notifier)
from poolwatch.safety import Alert, Severity

CRIT = Alert(Severity.CRITICAL, "in_water_unsupervised", "1 person(s) in the pool", 1, 0)
WARN = Alert(Severity.WARNING, "gate_quiet_hours", "Someone at the gate", 0, 0)


class FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port
        self.calls = []
        self.sent = []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        self.calls.append("starttls")

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def send_message(self, msg):
        self.sent.append(msg)


@pytest.fixture(autouse=True)
def _reset():
    FakeSMTP.instances = []


def email(min_severity=Severity.WARNING, factory=FakeSMTP):
    return EmailNotifier(("tim@example.com",), "smtp.example.com", 587, "sender@example.com",
                         "app-pass", min_severity=min_severity, smtp_factory=factory)


def test_email_sent_with_tls_login_and_frame(tmp_path):
    frame = tmp_path / "frame_01.jpg"
    frame.write_bytes(b"\xff\xd8jpeg")
    email().send(CRIT, frame)
    smtp = FakeSMTP.instances[0]
    assert smtp.calls == ["starttls", ("login", "sender@example.com", "app-pass")]
    msg = smtp.sent[0]
    assert msg["Subject"].startswith("[poolwatch] URGENT: 1 person(s) in the pool")
    assert msg["To"] == "tim@example.com" and msg["X-Priority"] == "1"
    attachments = list(msg.iter_attachments())
    assert attachments[0].get_filename() == "frame_01.jpg"
    assert "Camera frame attached." in msg.get_body(("plain",)).get_content()


def test_email_without_image():
    email().send(WARN)
    msg = FakeSMTP.instances[0].sent[0]
    assert list(msg.iter_attachments()) == []
    assert "X-Priority" not in msg


def test_email_min_severity_filters():
    email(min_severity=Severity.CRITICAL).send(WARN)
    assert FakeSMTP.instances == []


def test_email_failure_is_logged_not_raised(caplog):
    class Broken(FakeSMTP):
        def login(self, user, password):
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

    email(factory=Broken).send(CRIT)
    assert "email alert failed" in caplog.text


def test_multi_notifier_continues_after_failure():
    class Boom:
        def send(self, alert, image=None):
            raise RuntimeError("down")

    rec = RecordingNotifier()
    MultiNotifier([Boom(), rec]).send(CRIT)
    assert rec.sent[0][0] is CRIT


def test_build_notifier_channels(raw, monkeypatch):
    monkeypatch.delenv("POOLWATCH_SMTP_PASSWORD", raising=False)
    kinds = lambda cfg: [type(n) for n in build_notifier(config_from_dict(cfg).notify).notifiers]
    assert kinds(raw) == [ConsoleNotifier]
    raw["notify"] = {"ntfy_topic": "t", "email_to": "tim@example.com", "smtp_user": "s@example.com"}
    assert kinds(raw) == [ConsoleNotifier, NtfyNotifier]          # no password yet: email off
    monkeypatch.setenv("POOLWATCH_SMTP_PASSWORD", "app-pass")
    assert kinds(raw) == [ConsoleNotifier, NtfyNotifier, EmailNotifier]


def test_notify_config_validation(raw):
    raw["notify"] = {"email_to": "one@example.com", "email_min_severity": "critical"}
    n = config_from_dict(raw).notify
    assert n.email_to == ("one@example.com",) and n.email_min_severity == "CRITICAL"
    raw["notify"] = {"email_min_severity": "loud"}
    with pytest.raises(ValueError, match="email_min_severity"):
        config_from_dict(raw)
