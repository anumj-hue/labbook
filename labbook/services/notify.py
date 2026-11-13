"""In-app and email notifications (spec §4.10).

notify() adds the in-app row to the current transaction and renders the email straight away, but the
email is only sent after that transaction commits (and dropped if it rolls back). So a refused or
failed action never emails anyone. Rendering up front matters: objects are expired after commit.
"""

from dataclasses import dataclass

from flask import render_template
from sqlalchemy import event, select

from ..extensions import db
from ..models import Booking, Notification, Role, User
from ..timeutil import fmt_date
from .availability import type_timezone
from .mail import absolute, external_url, send_mail_message, url_path

# Event types. Non-critical emails (spec §4.10 †) map to the user's email preference key.
BOOKING_CONFIRMED = "booking_confirmed"
REQUEST_SUBMITTED = "request_submitted"
NEW_REQUEST = "new_request"
REQUEST_APPROVED = "request_approved"
REQUEST_REJECTED = "request_rejected"
REQUEST_EXPIRED = "request_expired"
CANCELLED_BY_STAFF = "cancelled_by_staff"
BUMPED = "bumped"
REMINDER = "reminder"
DIGEST = "digest"

OPTIONAL_EMAIL_PREF = {REMINDER: "reminders", DIGEST: "digest"}

_PENDING_KEY = "labbook_pending_mail"


@dataclass(frozen=True)
class _Mail:
    to: str
    subject: str
    body: str


def notify(user: User | None, kind: str, title: str, body: str, url: str | None = None, *,
           in_app: bool = True, email: bool = True) -> None:
    if user is None or not user.is_active:
        return
    if in_app:
        db.session.add(Notification(user_id=user.id, type=kind, payload={"title": title, "body": body, "url": url}))
    pref = OPTIONAL_EMAIL_PREF.get(kind)
    if email and (pref is None or user.email_pref(pref)):
        text = render_template("email/notification.txt", user=user, body=body,
                               link=absolute(url) if url else None,
                               prefs_link=external_url("auth.profile"))
        db.session.info.setdefault(_PENDING_KEY, []).append(_Mail(user.email, f"{title} · LabBook", text))


@event.listens_for(db.session, "after_commit")
def _send_after_commit(session) -> None:
    for mail in session.info.pop(_PENDING_KEY, []):
        send_mail_message(mail.to, mail.subject, mail.body)


@event.listens_for(db.session, "after_soft_rollback")
def _drop_after_rollback(session, previous_transaction) -> None:
    if previous_transaction.parent is None:  # outermost rollback, not a savepoint
        session.info.pop(_PENDING_KEY, None)


# --- Booking helpers ------------------------------------------------------------------------------

def when(booking: Booking) -> str:
    tz = type_timezone(booking.type)
    start, end = booking.starts_at.astimezone(tz), booking.ends_at.astimezone(tz)
    return f"{fmt_date(start.date())}, {start:%H:%M}–{end:%H:%M}"


def booking_path(booking: Booking) -> str:
    return url_path("bookings.detail", booking_id=booking.id)


def approvers() -> list[User]:
    return list(db.session.scalars(
        select(User).where(User.role.in_((Role.TEACHER, Role.ADMIN)), User.active.is_(True),
                           User.anonymised_at.is_(None))
    ))
