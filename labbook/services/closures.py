"""Bookings invalidated by calendar or opening-hours changes (spec FR-T4).

Admin views apply the change (flushed, not committed), call affected_bookings(), and if any are found
show "N bookings will be cancelled" and roll back. Once the admin confirms, the change is applied again
and cancel_affected() cancels and notifies them.
"""

from datetime import date, datetime, time, timedelta

from sqlalchemy import select

from ..extensions import db
from ..models import ACTIVE_STATUSES, Booking, BookingStatus, User
from ..timeutil import default_tz, utcnow
from . import notify
from .bookings import cancel
from .calendar import LabDayCache


def affected_bookings(start_day: date | None = None, end_day: date | None = None,
                      lab_id: int | None = None) -> list[Booking]:
    """Future, not-yet-collected bookings that no longer fit their lab's opening hours."""
    now = utcnow()
    tz = default_tz()
    stmt = select(Booking).where(Booking.status.in_(ACTIVE_STATUSES), Booking.status != BookingStatus.CHECKED_OUT,
                                 Booking.starts_at > now).order_by(Booking.starts_at)
    if start_day is not None:
        stmt = stmt.where(Booking.ends_at > datetime.combine(start_day, time.min, tz))
    if end_day is not None:
        stmt = stmt.where(Booking.starts_at < datetime.combine(end_day + timedelta(days=1), time.min, tz))
    db.session.expire_all()  # see the flushed change, not cached opening hours
    cache = LabDayCache()
    result = []
    for booking in db.session.scalars(stmt):
        lab = booking.unit.lab
        if lab_id is not None and lab.id != lab_id:
            continue
        day = booking.starts_at.astimezone(tz).date()
        if not cache.get(lab, day).contains(booking.starts_at, booking.ends_at):
            result.append(booking)
    return result


def cancel_affected(bookings: list[Booking], actor: User, reason: str) -> int:
    for booking in bookings:
        cancel(booking, actor, reason, kind=notify.CLOSURE_CANCELLED, title="Booking cancelled: lab closed")
    return len(bookings)
