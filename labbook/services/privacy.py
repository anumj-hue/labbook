"""Versioned privacy notice and consent (FR-D1)."""

from flask import render_template
from sqlalchemy import func, select

from ..extensions import db
from ..models import PrivacyNotice, User
from ..timeutil import utcnow
from . import audit


def current_notice() -> PrivacyNotice:
    """Latest published notice; publishes the bundled default as version 1 if none exists."""
    notice = db.session.scalar(select(PrivacyNotice).order_by(PrivacyNotice.version.desc()).limit(1))
    if notice is None:
        notice = PrivacyNotice(version=1, body_md=render_template("privacy_default.md"))
        db.session.add(notice)
        db.session.flush()
    return notice


def publish(actor: User, body_md: str) -> PrivacyNotice:
    latest = db.session.scalar(select(func.max(PrivacyNotice.version))) or 0
    notice = PrivacyNotice(version=latest + 1, body_md=body_md, published_by_id=actor.id)
    db.session.add(notice)
    audit.record(actor, "PrivacyNotice", notice.version, "publish", None, {"version": notice.version})
    return notice


def needs_consent(user: User) -> bool:
    return user.consent_version != current_notice().version


def record_consent(user: User) -> None:
    user.consent_at = utcnow()
    user.consent_version = current_notice().version
