"""SMTP delivery with a fake server (no network)."""
import dataclasses
import smtplib

import pytest

from app import config, mailer

GMAIL = "SMTP_HOST=smtp.gmail.com\nSMTP_PORT=587\nSMTP_USER=me@gmail.com\nSMTP_PASSWORD=abcd efgh ijkl mnop\n"


class FakeSMTP:
    instances: list["FakeSMTP"] = []
    fail_login = False

    def __init__(self, host, port, timeout=None, **kw):
        self.host, self.port, self.calls, self.sent = host, port, [], []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self, context=None):
        self.calls.append("starttls")

    def login(self, user, password):
        self.calls.append(("login", user, password))
        if FakeSMTP.fail_login:
            raise smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted")

    def send_message(self, msg):
        self.sent.append(msg)


@pytest.fixture()
def env(monkeypatch, tmp_path):
    path = tmp_path / ".env"
    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, env_file=path, outbox_dir=tmp_path / "outbox"))
    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)
    FakeSMTP.instances.clear()
    FakeSMTP.fail_login = False
    return path


def outbox_files():
    return list(config.settings.outbox_dir.glob("*.eml")) if config.settings.outbox_dir.exists() else []


def test_without_credentials_mail_goes_to_outbox(env):
    env.write_text("SMTP_HOST=smtp.gmail.com\nSMTP_USER=me@gmail.com\nSMTP_PASSWORD=\n")   # password not filled in yet
    assert mailer.delivery_mode() == "console"
    mailer.send_password_reset("user@example.com", "http://x/reset-password?token=t")
    assert FakeSMTP.instances == [] and len(outbox_files()) == 1


def test_gmail_delivery(env):
    env.write_text(GMAIL)
    assert mailer.delivery_mode() == "smtp"
    mailer.send_verification("user@example.com", "Sam", "http://x/verify-email?token=t")
    smtp = FakeSMTP.instances[-1]
    assert (smtp.host, smtp.port) == ("smtp.gmail.com", 587)
    assert smtp.calls == ["starttls", ("login", "me@gmail.com", "abcdefghijklmnop")]      # app-password spaces removed
    msg = smtp.sent[0]
    assert msg["To"] == "user@example.com" and msg["From"] == "Ledgerly <me@gmail.com>"  # From defaults to the Gmail account
    assert msg["Message-ID"] and msg.get_body(("html",)) is not None
    assert outbox_files() == []


def test_settings_follow_env_edits(env):
    env.write_text(GMAIL)
    assert config.settings.smtp_enabled
    env.write_text(GMAIL.replace("SMTP_PASSWORD=abcd efgh ijkl mnop", "SMTP_PASSWORD="))
    assert not config.settings.smtp_enabled


def test_failed_send_keeps_a_copy(env):
    env.write_text(GMAIL)
    FakeSMTP.fail_login = True
    mailer.send_password_reset("user@example.com", "http://x/reset-password?token=t")   # must not raise
    assert len(outbox_files()) == 1


def test_cli_explains_gmail_auth_errors(env, capsys):
    env.write_text(GMAIL)
    FakeSMTP.fail_login = True
    assert mailer.main(["test", "me@gmail.com"]) == 1
    out = capsys.readouterr().out
    assert "App Password" in out and "535" in out
    FakeSMTP.fail_login = False
    assert mailer.main(["test", "me@gmail.com"]) == 0 and "OK: sent" in capsys.readouterr().out
    env.write_text("")
    assert mailer.main(["test", "me@gmail.com"]) == 1 and "not configured" in capsys.readouterr().out
