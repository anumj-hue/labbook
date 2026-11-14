"""Outgoing email. Backends: smtp (real), outbox (.eml files in instance/outbox), memory (tests)."""

import logging
import re
import smtplib
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urljoin

from flask import current_app, has_request_context, render_template, url_for

from ..timeutil import utcnow

log = logging.getLogger(__name__)


def url_path(endpoint: str, **values) -> str:
    """Site-relative URL that works in requests, CLI commands and jobs alike."""
    if has_request_context():
        return url_for(endpoint, **values)
    with current_app.test_request_context():
        return url_for(endpoint, **values)


def absolute(path: str) -> str:
    return urljoin(current_app.config["BASE_URL"].rstrip("/") + "/", path.lstrip("/"))


def external_url(endpoint: str, **values) -> str:
    """Absolute URL built from BASE_URL, for links in emails."""
    return absolute(url_path(endpoint, **values))


def outbox() -> list[EmailMessage]:
    """Messages captured by the memory backend."""
    return current_app.extensions.setdefault("mail_outbox", [])


def send_mail(to: str, subject: str, template: str, **context) -> bool:
    body = render_template(f"email/{template}.txt", subject=subject, **context)
    return send_mail_message(to, subject, body, template)


def _message(to: str, subject: str, body: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = current_app.config["MAIL_FROM"]
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    return msg


def send_mail_message(to: str, subject: str, body: str, kind: str = "notification") -> bool:
    backend = current_app.config["MAIL_BACKEND"]
    try:
        _deliver(_message(to, subject, body))
    except (OSError, smtplib.SMTPException):
        # No recipient address in logs (spec §8: no PII in logs).
        log.exception("Failed to send email %r via %s", kind, backend)
        return False
    return True


def send_test(to: str) -> str | None:
    """Send a test email. Returns None on success, else a short error for the Admin (no secrets)."""
    body = "This is a test email from LabBook. If you can read it, email is working."
    try:
        _deliver(_message(to, "LabBook test email", body))
    except (OSError, smtplib.SMTPException, RuntimeError) as err:
        log.exception("Test email failed via %s", current_app.config["MAIL_BACKEND"])
        return f"{type(err).__name__}: {err}"
    return None


def settings_summary() -> str:
    cfg = current_app.config
    if cfg["MAIL_BACKEND"] != "smtp":
        return f"backend {cfg['MAIL_BACKEND']}"
    login = "with login" if cfg["SMTP_USER"] else "no login"
    return f"{cfg['SMTP_HOST']}:{cfg['SMTP_PORT']}, {login}, from {cfg['MAIL_FROM']}"


def _deliver(msg: EmailMessage) -> None:
    backend = current_app.config["MAIL_BACKEND"]
    if backend == "memory":
        outbox().append(msg)
    elif backend == "outbox":
        folder = Path(current_app.instance_path) / "outbox"
        folder.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^a-z0-9]+", "-", str(msg["Subject"]).lower()).strip("-")[:40]
        path = folder / f"{utcnow():%Y%m%d-%H%M%S-%f}-{slug}.eml"
        path.write_bytes(msg.as_bytes())
        log.info("Email written to outbox: %s", path.name)
    elif backend == "smtp":
        cfg = current_app.config
        # Port 465 is TLS from the first byte; 587 upgrades with STARTTLS.
        smtp_class = smtplib.SMTP_SSL if cfg["SMTP_PORT"] == 465 else smtplib.SMTP
        with smtp_class(cfg["SMTP_HOST"], cfg["SMTP_PORT"], timeout=15) as smtp:
            if cfg["SMTP_STARTTLS"] and smtp_class is smtplib.SMTP:
                smtp.starttls()
            if cfg["SMTP_USER"]:
                smtp.login(cfg["SMTP_USER"], cfg["SMTP_PASSWORD"] or "")
            smtp.send_message(msg)
    else:
        raise RuntimeError(f"Unknown MAIL_BACKEND {backend!r}")
