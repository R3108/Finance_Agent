"""Transactional email (verification, password reset, security notices).

With SMTP_HOST, SMTP_USER and SMTP_PASSWORD set, mail is sent over SMTP (Gmail, SES, SendGrid, Brevo…);
the settings are re-read from .env when it changes, so no restart is needed. Otherwise messages are written
to the server log and `data/outbox/*.eml` so development works with no provider. A failed send is also
saved to the outbox. Check a setup with `python -m app.mailer test you@example.com`. Links are NEVER returned in API responses: that would let anyone reset any account's password.
"""
from __future__ import annotations

import logging
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from html import escape

from . import config

log = logging.getLogger("ledgerly.mail")


def delivery_mode() -> str:
    return "smtp" if config.settings.smtp_enabled else "console"


def _render(title: str, intro: str, button: str, url: str, outro: str) -> tuple[str, str]:
    text = f"{title}\n\n{intro}\n\n{button}: {url}\n\n{outro}\n\n— Ledgerly"
    html = f"""<!doctype html><html><body style="margin:0;background:#f6f6f3;font-family:system-ui,-apple-system,'Segoe UI',sans-serif;color:#0b0b0b">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:32px 16px">
<table role="presentation" width="100%" style="max-width:480px;background:#fcfcfb;border:1px solid #e1e0d9;border-radius:12px" cellpadding="0" cellspacing="0">
<tr><td style="padding:28px">
<div style="font-weight:700;font-size:16px;margin-bottom:20px"><span style="display:inline-block;width:24px;height:24px;border-radius:6px;background:#2a78d6;color:#fff;text-align:center;line-height:24px;margin-right:8px">L</span>Ledgerly</div>
<h1 style="font-size:20px;margin:0 0 12px">{escape(title)}</h1>
<p style="font-size:14px;line-height:1.55;color:#52514e;margin:0 0 20px">{escape(intro)}</p>
<a href="{escape(url, quote=True)}" style="display:inline-block;background:#2a78d6;color:#fff;text-decoration:none;padding:10px 18px;border-radius:8px;font-weight:600;font-size:14px">{escape(button)}</a>
<p style="font-size:12px;line-height:1.55;color:#898781;margin:20px 0 0">{escape(outro)}<br><br>Button not working? Paste this link into your browser:<br><span style="word-break:break-all">{escape(url)}</span></p>
</td></tr></table></td></tr></table></body></html>"""
    return text, html


def _build(to: str, subject: str, text: str, html: str | None) -> EmailMessage:
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = config.settings.email_from, to, subject
    msg["Date"], msg["Message-ID"] = formatdate(localtime=True), make_msgid(domain="ledgerly")
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    return msg


def _to_outbox(msg: EmailMessage, text: str, reason: str) -> None:
    s = config.settings
    s.outbox_dir.mkdir(parents=True, exist_ok=True)
    path = s.outbox_dir / f"{datetime.now():%Y%m%d-%H%M%S-%f}.eml"
    path.write_bytes(bytes(msg))
    log.warning("EMAIL (%s, not sent) to=%s subject=%r file=%s\n%s", reason, msg["To"], msg["Subject"], path, text)


def smtp_send(msg: EmailMessage) -> None:
    """Deliver over SMTP, raising on any failure (the caller decides what to do)."""
    s = config.settings
    ctx = ssl.create_default_context()
    if s.smtp_port == 465:  # implicit TLS
        with smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=15, context=ctx) as smtp:
            smtp.login(s.smtp_user, s.smtp_password)
            smtp.send_message(msg)
        return
    with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15) as smtp:
        if s.smtp_starttls:
            smtp.starttls(context=ctx)
        smtp.login(s.smtp_user, s.smtp_password)
        smtp.send_message(msg)


def send(to: str, subject: str, text: str, html: str | None = None) -> None:
    msg = _build(to, subject, text, html)
    if not config.settings.smtp_enabled:
        _to_outbox(msg, text, "dev outbox")
        return
    try:
        smtp_send(msg)
        log.info("Email sent to %s: %s", to, subject)
    except (smtplib.SMTPException, OSError) as exc:
        # never fail the request (or reveal account existence) on mail errors, and never lose the link:
        # keep a copy in the outbox so an operator can forward it
        log.error("Email to %s failed (%s): %s", to, explain(exc), exc)
        _to_outbox(msg, text, "SMTP failed")


def explain(exc: Exception) -> str:
    """Plain-English hint for common SMTP errors (Gmail in particular)."""
    code = getattr(exc, "smtp_code", None)
    if isinstance(exc, smtplib.SMTPAuthenticationError) or code == 535:
        return ("login rejected. For Gmail, SMTP_PASSWORD must be a 16-character App Password "
                "(myaccount.google.com/apppasswords, needs 2-Step Verification), not your normal password, "
                "and SMTP_USER must be the full Gmail address it was created for")
    if code in (550, 552, 554) or isinstance(exc, smtplib.SMTPRecipientsRefused):
        return "the server refused the message or recipient (daily sending limit reached, or a bad address)"
    if isinstance(exc, smtplib.SMTPSenderRefused):
        return "sender refused: for Gmail, EMAIL_FROM must be your Gmail address or a verified 'Send mail as' alias"
    if isinstance(exc, (OSError, smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected)):
        return "couldn't connect. Check SMTP_HOST/SMTP_PORT (Gmail: smtp.gmail.com, 587) and that a firewall or antivirus isn't blocking outbound mail"
    return "unexpected SMTP error"


def main(argv: list[str] | None = None) -> int:
    """`python -m app.mailer test you@example.com`: send one real email and report exactly what happened."""
    import sys
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 2 or args[0] != "test":
        print("usage: python -m app.mailer test you@example.com")
        return 2
    s = config.settings
    missing = [k for k, v in (("SMTP_HOST", s.smtp_host), ("SMTP_USER", s.smtp_user), ("SMTP_PASSWORD", s.smtp_password)) if not v]
    if missing:
        print(f"SMTP is not configured: {', '.join(missing)} empty in {s.env_file}. Emails currently go to {s.outbox_dir}.")
        return 1
    print(f"Sending via {s.smtp_host}:{s.smtp_port} as {s.smtp_user}, From: {s.email_from} -> {args[1]} ...")
    if s.smtp_host.lower() == "smtp.gmail.com" and s.smtp_user.lower() not in s.email_from.lower():
        print("  note: Gmail replaces the From address with your Gmail account unless it's a verified 'Send mail as' alias.")
    text, html = _render("SMTP is working", "This test email from Ledgerly arrived, so verification and password-reset emails will too.",
                         "Open Ledgerly", s.app_url, "You can delete this email.")
    try:
        smtp_send(_build(args[1], "Ledgerly test email", text, html))
    except (smtplib.SMTPException, OSError) as exc:
        print(f"FAILED: {explain(exc)}\n  raw error: {exc}")
        return 1
    print("OK: sent. Check the inbox (and the spam folder the first time).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


def send_verification(to: str, name: str, url: str) -> None:
    text, html = _render(f"Confirm your email, {name}", "Tap the button below to confirm this is your email address.",
                         "Confirm email", url, "This link expires in 24 hours. If you didn't create a Ledgerly account, ignore this email.")
    send(to, "Confirm your Ledgerly email", text, html)


def send_password_reset(to: str, url: str) -> None:
    text, html = _render("Reset your password", "Someone (hopefully you) asked to reset the password for your Ledgerly account.",
                         "Choose a new password", url,
                         "This link works once and expires in 1 hour. If you didn't ask for this, you can ignore this email — your password won't change.")
    send(to, "Reset your Ledgerly password", text, html)


def send_renewal_reminder(to: str, name: str, ends_on: str, url: str) -> None:
    text, html = _render(f"Your Ledgerly Pro ends on {ends_on}", f"Hi {name}, your prepaid Pro pass runs out on {ends_on}. "
                         "Renew now to keep your debt planner, challenges, Money Wrapped and unlimited AI questions. "
                         "Renewing early adds the new period after your current one, so you don't lose any days.",
                         "Renew Pro", url, "If you don't renew, your account simply moves to the Free plan. Your data stays safe.")
    send(to, f"Your Ledgerly Pro ends on {ends_on}", text, html)


def _list_block(items: list[tuple[str, str]]) -> str:
    """Rows of title + supporting line, styled to match `_render`."""
    return "".join(
        f'<tr><td style="padding:10px 0;border-top:1px solid #ece9e2">'
        f'<div style="font-size:14px;font-weight:600">{escape(title)}</div>'
        f'<div style="font-size:13px;color:#52514e;line-height:1.5;margin-top:2px">{escape(body)}</div>'
        f"</td></tr>"
        for title, body in items)


def _wrap(title: str, lead: str, inner: str, button: str, url: str, outro: str) -> str:
    return f"""<!doctype html><html><body style="margin:0;background:#f6f6f3;font-family:system-ui,-apple-system,'Segoe UI',sans-serif;color:#0b0b0b">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:32px 16px">
<table role="presentation" width="100%" style="max-width:520px;background:#fcfcfb;border:1px solid #e1e0d9;border-radius:12px" cellpadding="0" cellspacing="0">
<tr><td style="padding:28px">
<div style="font-weight:700;font-size:16px;margin-bottom:20px"><span style="display:inline-block;width:24px;height:24px;border-radius:6px;background:#2a78d6;color:#fff;text-align:center;line-height:24px;margin-right:8px">L</span>Ledgerly</div>
<h1 style="font-size:20px;margin:0 0 12px">{escape(title)}</h1>
<p style="font-size:14px;line-height:1.55;color:#52514e;margin:0 0 16px">{escape(lead)}</p>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{inner}</table>
<a href="{escape(url, quote=True)}" style="display:inline-block;margin-top:22px;background:#2a78d6;color:#fff;text-decoration:none;padding:10px 18px;border-radius:8px;font-weight:600;font-size:14px">{escape(button)}</a>
<p style="font-size:12px;line-height:1.55;color:#898781;margin:20px 0 0">{escape(outro)}</p>
</td></tr></table></td></tr></table></body></html>"""


def send_alerts(to: str, name: str, items: list[dict], url: str) -> None:
    """Immediate notice for high-severity alerts. Everything quieter waits for the digest."""
    headline = items[0]["title"] if len(items) == 1 else f"{len(items)} things need your attention"
    lead = f"Hi {name}, Ledgerly spotted something while watching your accounts."
    rows = [(i["title"], i["body"]) for i in items]
    text = f"{headline}\n\n{lead}\n\n" + "\n\n".join(f"{t}\n{b}" for t, b in rows) + f"\n\nOpen Ledgerly: {url}\n\n— Ledgerly"
    html = _wrap(headline, lead, _list_block(rows), "Open Ledgerly", url,
                 "You're getting this because you set up alerts. Change them any time under Automations.")
    send(to, f"Ledgerly alert: {headline}", text, html)


def send_digest(to: str, name: str, digest: dict, narration: str | None, url: str) -> None:
    """The recurring money summary. Every figure comes from `automations.build_digest`."""
    span = "week" if digest["period"] == "weekly" else "month"
    title = f"Your Ledgerly {span}"
    change = digest.get("change_pct")
    direction = "about the same as" if change is None or abs(change) < 5 else (
        f"{abs(change):.0f}% {'more' if change > 0 else 'less'} than")
    lead = narration or (f"Hi {name}, here's how your {span} went.")

    money = digest["text"]   # already written in the reader's own currency

    rows: list[tuple[str, str]] = [
        (f"Spent {money['spent']} in the last {digest['days']} days",
         f"That's {direction} the {span} before ({money['prior_spent']})."),
        (f"Health score {digest['health_score']}/100 ({digest['health_grade']})",
         f"Safe to spend {money['safe_to_spend']} over the next {digest['days_left']} days "
         f"— about {money['per_day']} a day."),
    ]
    if digest["top_categories"]:
        top = ", ".join(f"{c['category']} {c['amount_text']}" for c in digest["top_categories"])
        rows.append(("Where it went", top))
    rows += [(a["title"], a["body"]) for a in digest["alerts"][:4]]

    text = f"{title}\n\n{lead}\n\n" + "\n\n".join(f"{t}\n{b}" for t, b in rows) + f"\n\nOpen Ledgerly: {url}\n\n— Ledgerly"
    html = _wrap(title, lead, _list_block(rows), "See the full picture", url,
                 "Change how often you get this — or turn it off — under Automations in Ledgerly.")
    send(to, f"{title}: {money['spent']} spent", text, html)


def send_household_invite(to: str, household: str, invited_by: str | None, role: str, url: str) -> None:
    who = f"{invited_by} has" if invited_by else "You've been"
    seeing = ("see the household's spending, budgets and goals (read-only)" if role == "viewer"
              else "share a ledger: combined spending, budgets and goals")
    text, html = _render(
        f"Join {household} on Ledgerly",
        f"{who} invited you to {household}. Accept and you'll {seeing}. "
        "Accounts you mark private stay private — they never appear in the shared view.",
        "Accept invite", url,
        "This invite works once and expires in 7 days. If you weren't expecting it, ignore this email.")
    send(to, f"Join {household} on Ledgerly", text, html)


def send_security_notice(to: str, subject: str, body: str) -> None:
    """A plain notice about a change to how the account is protected (2FA on/off, recovery code used)."""
    send(to, f"Ledgerly security: {subject}", f"{body}\n\n— Ledgerly")


def send_password_changed(to: str) -> None:
    send(to, "Your Ledgerly password was changed",
         "The password for your Ledgerly account was just changed and all other sessions were signed out.\n\n"
         "If this wasn't you, reset your password immediately from the sign-in page.\n\n— Ledgerly")
