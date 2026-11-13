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


def send_mail_message(to: str, subject: str, body: str, kind: str = "notification") -> bool:
    msg = EmailMessage()
    msg["From"] = current_app.config["MAIL_FROM"]
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    backend = current_app.config["MAIL_BACKEND"]
    try:
        if backend == "memory":
            outbox().append(msg)
        elif backend == "outbox":
            folder = Path(current_app.instance_path) / "outbox"
            folder.mkdir(parents=True, exist_ok=True)
            slug = re.sub(r"[^a-z0-9]+", "-", subject.lower()).strip("-")[:40]
            path = folder / f"{utcnow():%Y%m%d-%H%M%S-%f}-{slug}.eml"
            path.write_bytes(msg.as_bytes())
            log.info("Email written to outbox: %s", path.name)
        elif backend == "smtp":
            cfg = current_app.config
            with smtplib.SMTP(cfg["SMTP_HOST"], cfg["SMTP_PORT"], timeout=15) as smtp:
                if cfg["SMTP_STARTTLS"]:
                    smtp.starttls()
                if cfg["SMTP_USER"]:
                    smtp.login(cfg["SMTP_USER"], cfg["SMTP_PASSWORD"] or "")
                smtp.send_message(msg)
        else:
            raise RuntimeError(f"Unknown MAIL_BACKEND {backend!r}")
    except (OSError, smtplib.SMTPException):
        # No recipient address in logs (spec §8: no PII in logs).
        log.exception("Failed to send email %r via %s", kind, backend)
        return False
    return True
